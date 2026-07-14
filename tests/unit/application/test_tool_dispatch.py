import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.application.errors import ToolDispatchError
from switchboard.application.ports.agent_orchestrator import ToolCallAwaitingApproval
from switchboard.application.ports.model_gateway import CallTool
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
)
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.application.services.tool_dispatch import (
    DurableToolCallHandler,
    ToolDispatchContext,
)
from switchboard.domain.agents import AgentVersion
from switchboard.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    PolicyEvaluationRecord,
)
from switchboard.domain.context import ContextPolicy
from switchboard.domain.conversations import Conversation, ConversationStatus
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    AgentDefinitionId,
    AgentVersionId,
    ApprovalRequestId,
    ConversationId,
    ExecutionEventId,
    MessageId,
    PolicyEvaluationId,
    TeamId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.policy import PolicyDecision
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    EligibleTool,
    IdempotencyMode,
    ReconciliationMode,
    RetryPolicy,
    ToolDefinition,
    ToolEffect,
    ToolLifecycleStatus,
    ToolManifest,
    ToolVersion,
    ToolVersionState,
)
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus

NOW = datetime(2026, 7, 14, 23, 30, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Generator[IdentifierT]:
    def __init__(self, constructor: object) -> None:
        self._constructor = constructor

    def new(self) -> IdentifierT:
        constructor = cast("type[IdentifierT]", self._constructor)
        return constructor(uuid4())


class RecordingAdapter:
    def __init__(self, state: "FakeState") -> None:
        self._state = state
        self.mode = "success"
        self.called = 0
        self.cancelled = asyncio.Event()

    async def invoke(
        self,
        request: ToolInvocationRequest,
    ) -> ToolInvocationSuccess | ToolInvocationFailure:
        assert not self._state.transaction_open
        self.called += 1
        assert request.idempotency_key is not None
        if self.mode == "wait":
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if self.mode == "exception":
            raise RuntimeError("provider-secret-must-not-escape")
        if self.mode == "failure":
            return ToolInvocationFailure("temporarily_unavailable", retryable=True)
        if self.mode == "invalid_output":
            return ToolInvocationSuccess({"secret": "output-secret"})
        return ToolInvocationSuccess({"items": [{"id": "WI-1"}]})

    async def reconcile(self, idempotency_key: str) -> object:
        raise AssertionError(f"unexpected reconciliation for {idempotency_key}")


class FakeResolver:
    def __init__(self, adapter: RecordingAdapter | None) -> None:
        self.adapter = adapter

    def resolve(self, adapter_key: str) -> RecordingAdapter | None:
        assert adapter_key == "reference.search.v1"
        return self.adapter


class FakeState:
    def __init__(self) -> None:
        self.transaction_open = False
        self.commits = 0
        self.invocation: ToolInvocation | None = None
        self.evaluations: list[PolicyEvaluationRecord] = []
        self.approvals: list[ApprovalRequest] = []
        self.events: list[tuple[ExecutionEventKind, dict[str, object]]] = []
        self.disable_after_pending = False
        self.fail_event_append = False
        self.turn, self.attempt, self.conversation, self.eligible, self.version_state = records()


class FakeToolInvocations:
    def __init__(self, unit_of_work: "FakeUnitOfWork") -> None:
        self._unit_of_work = unit_of_work

    async def add(self, invocation: ToolInvocation) -> None:
        self._unit_of_work.pending_invocation = invocation

    async def update_lifecycle(
        self,
        *,
        previous: ToolInvocation,
        updated: ToolInvocation,
    ) -> None:
        current = self._unit_of_work.pending_invocation or self._unit_of_work.state.invocation
        assert current == previous
        self._unit_of_work.updated_invocation = updated


class FakeApprovals:
    def __init__(self, unit_of_work: "FakeUnitOfWork") -> None:
        self._unit_of_work = unit_of_work

    async def add_evaluation(self, evaluation: PolicyEvaluationRecord) -> None:
        self._unit_of_work.pending_evaluations.append(evaluation)

    async def add_request(self, approval: ApprovalRequest) -> None:
        self._unit_of_work.pending_approvals.append(approval)


class FakeTurns:
    def __init__(self, unit_of_work: "FakeUnitOfWork") -> None:
        self._unit_of_work = unit_of_work

    async def get(self, turn_id: TurnId) -> Turn | None:
        return (
            self._unit_of_work.state.turn if turn_id == self._unit_of_work.state.turn.id else None
        )

    async def get_attempt(self, attempt_id: TurnAttemptId) -> TurnAttempt | None:
        state = self._unit_of_work.state
        return state.attempt if attempt_id == state.attempt.id else None

    async def append_event(self, **values: object) -> object:
        if self._unit_of_work.state.fail_event_append:
            raise RuntimeError("event append failed")
        kind = cast(ExecutionEventKind, values["kind"])
        payload = cast(dict[str, object], values["payload"])
        self._unit_of_work.pending_events.append((kind, payload))
        return SimpleNamespace(
            sequence=len(self._unit_of_work.state.events) + len(self._unit_of_work.pending_events)
        )

    async def update_turn_lifecycle(self, *, previous: Turn, updated: Turn) -> None:
        assert self._unit_of_work.state.turn == previous
        self._unit_of_work.updated_turn = updated

    async def update_attempt_lifecycle(
        self, *, previous: TurnAttempt, updated: TurnAttempt
    ) -> None:
        assert self._unit_of_work.state.attempt == previous
        self._unit_of_work.updated_attempt = updated


class FakeConversations:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        return self._state.conversation if conversation_id == self._state.conversation.id else None


class FakeTools:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def list_eligible_for_agent(self, **values: object) -> tuple[EligibleTool, ...]:
        del values
        if self._state.version_state.status is not ToolLifecycleStatus.ACTIVE:
            return ()
        return (self._state.eligible,)

    async def get_version_state_for_update(
        self,
        tool_version_id: ToolVersionId,
    ) -> ToolVersionState | None:
        if tool_version_id != self._state.eligible.version.id:
            return None
        return self._state.version_state

    async def get_version(self, tool_version_id: ToolVersionId) -> ToolVersion | None:
        version = self._state.eligible.version
        return version if tool_version_id == version.id else None


class FakeUnitOfWork:
    def __init__(self, state: FakeState) -> None:
        self.state = state
        self.turns = FakeTurns(self)
        self.conversations = FakeConversations(state)
        self.tools = FakeTools(state)
        self.tool_invocations = FakeToolInvocations(self)
        self.approvals = FakeApprovals(self)
        self.pending_invocation: ToolInvocation | None = None
        self.updated_invocation: ToolInvocation | None = None
        self.updated_turn: Turn | None = None
        self.updated_attempt: TurnAttempt | None = None
        self.pending_evaluations: list[PolicyEvaluationRecord] = []
        self.pending_approvals: list[ApprovalRequest] = []
        self.pending_events: list[tuple[ExecutionEventKind, dict[str, object]]] = []

    async def __aenter__(self) -> "FakeUnitOfWork":
        assert not self.state.transaction_open
        self.state.transaction_open = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.state.transaction_open = False

    async def commit(self) -> None:
        if self.pending_invocation is not None:
            self.state.invocation = self.pending_invocation
        if self.updated_invocation is not None:
            self.state.invocation = self.updated_invocation
        if self.updated_turn is not None:
            self.state.turn = self.updated_turn
        if self.updated_attempt is not None:
            self.state.attempt = self.updated_attempt
        self.state.evaluations.extend(self.pending_evaluations)
        self.state.approvals.extend(self.pending_approvals)
        self.state.events.extend(self.pending_events)
        self.state.commits += 1
        if (
            self.state.disable_after_pending
            and self.state.invocation is not None
            and self.state.invocation.status is ToolInvocationStatus.PENDING
        ):
            self.state.version_state = replace(
                self.state.version_state,
                status=ToolLifecycleStatus.DISABLED,
                revision=self.state.version_state.revision + 1,
            )


class FakeFactory:
    def __init__(self, state: FakeState) -> None:
        self.state = state

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.state)


def object_schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def records() -> tuple[
    Turn,
    TurnAttempt,
    Conversation,
    EligibleTool,
    ToolVersionState,
]:
    team_id = TeamId(uuid4())
    agent_version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=AgentDefinitionId(uuid4()),
        version_number=1,
        context_policy=ContextPolicy(4_096, 512, 256, 256, 1),
        created_at=NOW,
    )
    conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id,
        default_agent_version_id=agent_version.id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=2,
        created_at=NOW,
        updated_at=NOW,
    )
    turn = Turn(
        id=TurnId(uuid4()),
        conversation_id=conversation.id,
        input_message_id=MessageId(uuid4()),
        agent_version_id=agent_version.id,
        status=TurnStatus.RUNNING,
        created_at=NOW,
    )
    attempt = TurnAttempt(
        id=TurnAttemptId(uuid4()),
        turn_id=turn.id,
        attempt_number=1,
        status=TurnAttemptStatus.RUNNING,
        created_at=NOW,
        started_at=NOW,
    )
    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=team_id,
        tool_key="search_work_items",
        created_at=NOW,
    )
    manifest = ToolManifest(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Search work items",
        description="Search deterministic work items.",
        input_schema=object_schema(
            {"query": {"type": "string"}, "secret": {"type": "string"}},
            ["query"],
        ),
        output_schema=object_schema(
            {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                }
            },
            ["items"],
        ),
        effect=ToolEffect.READ_ONLY,
        required_scopes=("work_items:read",),
        timeout_ms=10,
        retry_policy=RetryPolicy(1, 0, ()),
        idempotency=IdempotencyMode.NONE,
        reconciliation=ReconciliationMode.NONE,
        adapter_key="reference.search.v1",
    )
    version = ToolVersion(
        id=ToolVersionId(uuid4()),
        tool_definition_id=definition.id,
        version_number=1,
        manifest=manifest,
        content_hash=manifest.content_hash,
        created_at=NOW,
    )
    state = ToolVersionState(
        tool_version_id=version.id,
        status=ToolLifecycleStatus.ACTIVE,
        revision=2,
        activated_conformance_run_id=ToolConformanceRunId(uuid4()),
        created_at=NOW,
        updated_at=NOW,
    )
    return turn, attempt, conversation, EligibleTool(definition, version), state


def handler(
    state: FakeState,
    adapter: RecordingAdapter | None,
    *,
    granted_scopes: tuple[str, ...] = ("work_items:read",),
) -> DurableToolCallHandler:
    return DurableToolCallHandler(
        context=ToolDispatchContext(
            team_id=state.conversation.team_id,
            actor_id=ActorId(uuid4()),
            agent_version_id=state.turn.agent_version_id,
            turn_id=state.turn.id,
            attempt_id=state.attempt.id,
            granted_scopes=granted_scopes,
        ),
        unit_of_work_factory=cast(UnitOfWorkFactory, FakeFactory(state)),
        adapter_resolver=FakeResolver(adapter),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        invocation_ids=Generator[ToolInvocationId](ToolInvocationId),
        policy_evaluation_ids=Generator[PolicyEvaluationId](PolicyEvaluationId),
        approval_ids=Generator[ApprovalRequestId](ApprovalRequestId),
        event_ids=Generator[ExecutionEventId](ExecutionEventId),
    )


def action(state: FakeState, **arguments: object) -> CallTool:
    return CallTool(
        tool_version_id=state.eligible.version.id,
        arguments={"query": "overdue", "secret": "input-secret", **arguments},
    )


async def test_success_commits_identity_start_and_safe_completion_separately() -> None:
    state = FakeState()
    adapter = RecordingAdapter(state)

    result = await handler(state, adapter).execute(action(state))

    assert result.output == {"items": ({"id": "WI-1"},)}
    assert state.invocation is not None
    assert state.invocation.status is ToolInvocationStatus.SUCCEEDED
    assert state.commits == 3
    assert [kind for kind, _ in state.events] == [
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_COMPLETED,
    ]
    public_payload = repr(state.events)
    assert "input-secret" not in public_payload
    assert "WI-1" not in public_payload


def configure_mutating_tool(state: FakeState) -> None:
    manifest = replace(
        state.eligible.version.manifest,
        effect=ToolEffect.MUTATING,
        idempotency=IdempotencyMode.REQUIRED,
        reconciliation=ReconciliationMode.BY_IDEMPOTENCY_KEY,
    )
    state.eligible = EligibleTool(
        state.eligible.definition,
        replace(
            state.eligible.version,
            manifest=manifest,
            content_hash=manifest.content_hash,
        ),
    )


@pytest.mark.parametrize(
    ("configure", "granted_scopes", "arguments", "failure_code"),
    [
        (lambda state: None, (), {}, "tool_scope_denied"),
        (lambda state: None, ("work_items:read",), {"query": 7}, "tool_arguments_invalid"),
    ],
)
async def test_preflight_rejections_persist_nothing(
    configure: Callable[[FakeState], None],
    granted_scopes: tuple[str, ...],
    arguments: dict[str, object],
    failure_code: str,
) -> None:
    state = FakeState()
    configure(state)
    adapter = RecordingAdapter(state)

    with pytest.raises(ToolDispatchError) as raised:
        await handler(state, adapter, granted_scopes=granted_scopes).execute(
            action(state, **arguments)
        )

    assert raised.value.failure_code == failure_code
    assert state.invocation is None
    assert state.events == []
    assert adapter.called == 0


async def test_mutation_persists_a_safe_pause_without_dispatching() -> None:
    state = FakeState()
    configure_mutating_tool(state)
    adapter = RecordingAdapter(state)

    result = await handler(state, adapter).execute(action(state))

    assert isinstance(result, ToolCallAwaitingApproval)
    assert state.invocation is not None
    assert state.invocation.status is ToolInvocationStatus.AWAITING_CONFIRMATION
    assert state.turn.status is TurnStatus.AWAITING_CONFIRMATION
    assert state.attempt.status is TurnAttemptStatus.AWAITING_CONFIRMATION
    assert len(state.evaluations) == 1
    assert state.evaluations[0].evaluation.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert len(state.approvals) == 1
    assert state.approvals[0].status is ApprovalStatus.PENDING
    assert state.events[0][0] is ExecutionEventKind.APPROVAL_REQUIRED
    assert "input-secret" not in repr(state.events)
    assert adapter.called == 0
    assert state.transaction_open is False


@pytest.mark.parametrize(
    "effect",
    [ToolEffect.EXTERNAL_SIDE_EFFECT, ToolEffect.PRIVILEGED],
)
async def test_unsafe_effects_are_denied_with_audit_and_without_invocation(
    effect: ToolEffect,
) -> None:
    state = FakeState()
    manifest = replace(
        state.eligible.version.manifest,
        effect=effect,
        idempotency=IdempotencyMode.REQUIRED,
        reconciliation=ReconciliationMode.BY_IDEMPOTENCY_KEY,
    )
    state.eligible = EligibleTool(
        state.eligible.definition,
        replace(
            state.eligible.version,
            manifest=manifest,
            content_hash=manifest.content_hash,
        ),
    )
    adapter = RecordingAdapter(state)

    with pytest.raises(ToolDispatchError) as raised:
        await handler(state, adapter).execute(action(state))

    assert raised.value.failure_code == "tool_policy_denied"
    assert state.invocation is None
    assert len(state.evaluations) == 1
    assert state.evaluations[0].evaluation.decision is PolicyDecision.DENY
    assert state.approvals == []
    assert adapter.called == 0


async def test_disable_that_wins_lock_race_blocks_dispatch() -> None:
    state = FakeState()
    state.disable_after_pending = True
    adapter = RecordingAdapter(state)

    with pytest.raises(ToolDispatchError) as raised:
        await handler(state, adapter).execute(action(state))

    assert raised.value.failure_code == "tool_not_eligible"
    assert state.invocation is not None
    assert state.invocation.status is ToolInvocationStatus.PENDING
    assert state.events == []
    assert adapter.called == 0


@pytest.mark.parametrize(
    ("mode", "failure_code"),
    [
        ("timeout", "tool_timeout"),
        ("exception", "tool_adapter_error"),
        ("failure", "tool.temporarily_unavailable"),
        ("invalid_output", "tool_output_invalid"),
    ],
)
async def test_adapter_failures_commit_only_safe_terminal_data(
    mode: str,
    failure_code: str,
) -> None:
    state = FakeState()
    adapter = RecordingAdapter(state)
    adapter.mode = mode

    with pytest.raises(ToolDispatchError) as raised:
        await handler(state, adapter).execute(action(state))

    assert raised.value.failure_code == failure_code
    assert state.invocation is not None
    assert state.invocation.status is ToolInvocationStatus.FAILED
    assert state.invocation.failure_code == failure_code
    assert state.events[-1] == (
        ExecutionEventKind.TOOL_FAILED,
        {
            "invocation_id": str(state.invocation.id),
            "failure_code": failure_code,
        },
    )
    assert "provider-secret" not in repr(state.events)


async def test_start_transition_rolls_back_when_event_append_fails() -> None:
    state = FakeState()
    state.fail_event_append = True
    adapter = RecordingAdapter(state)

    with pytest.raises(RuntimeError, match="event append failed"):
        await handler(state, adapter).execute(action(state))

    assert state.invocation is not None
    assert state.invocation.status is ToolInvocationStatus.PENDING
    assert state.events == []
    assert adapter.called == 0


async def test_cancellation_leaves_committed_running_progress_for_recovery() -> None:
    state = FakeState()
    adapter = RecordingAdapter(state)
    adapter.mode = "wait"
    task = asyncio.create_task(handler(state, adapter).execute(action(state)))
    while adapter.called == 0:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert adapter.cancelled.is_set()
    assert state.invocation is not None
    assert state.invocation.status is ToolInvocationStatus.RUNNING
    assert [kind for kind, _ in state.events] == [ExecutionEventKind.TOOL_STARTED]
