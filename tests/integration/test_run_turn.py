import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.context.deterministic_summarizer import (
    DeterministicPrefixSummarizer,
)
from switchboard.adapters.models.deterministic import ScriptedModelGateway
from switchboard.adapters.orchestration.langgraph import LangGraphAgentOrchestrator
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.system import SystemClock
from switchboard.adapters.tools.reference import (
    SearchWorkItemsAdapter,
    search_work_items_manifest,
    update_due_date_manifest,
)
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.errors import (
    ModelGatewayUnavailableError,
    ToolDispatchError,
    TurnLifecycleConflictError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.model_gateway import CallTool, Respond
from switchboard.application.ports.tool_adapter import (
    ToolInvocationRequest,
    ToolInvocationSuccess,
    ToolReconciliationResult,
)
from switchboard.application.services.tool_manifest_validation import ToolManifestValidator
from switchboard.application.use_cases.build_turn_context import BuildTurnContext
from switchboard.application.use_cases.run_turn import RunTurn, RunTurnCommand
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.context import ContextItemCandidate
from switchboard.domain.errors import InvalidStateTransition
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    AgentToolBindingId,
    AgentVersionId,
    ApprovalRequestId,
    ConversationSummaryId,
    ExecutionEventId,
    MessageId,
    PolicyEvaluationId,
    TeamId,
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnId,
)
from switchboard.domain.policy import PolicyDecision
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.tools import (
    AgentToolBinding,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolDefinition,
    ToolManifestCandidate,
    ToolVersion,
)
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from tests.integration.support import seed_turn
from tests.integration.test_conversation_api import (
    command_headers,
    make_app,
    seed_agent,
)

NOW = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
ACTOR_ID = ActorId(uuid4())


class CharacterTokenCounter:
    @property
    def version(self) -> str:
        return "character-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Generator[IdentifierT]:
    def __init__(self, factory: Callable[[], IdentifierT]) -> None:
        self._factory = factory

    def new(self) -> IdentifierT:
        return self._factory()


def context_builder(factory: SqlAlchemyUnitOfWorkFactory) -> BuildTurnContext:
    counter = CharacterTokenCounter()
    return BuildTurnContext(
        unit_of_work_factory=factory,
        token_counter=counter,
        summarizer=DeterministicPrefixSummarizer(counter),
        clock=FixedClock(),
        summary_ids=Generator(lambda: ConversationSummaryId(uuid4())),
    )


def run_turn(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    gateway: ScriptedModelGateway,
    resolver: StaticToolAdapterResolver,
    message_id_factory: Callable[[], MessageId] | None = None,
    clock: Clock | None = None,
) -> RunTurn:
    return RunTurn(
        unit_of_work_factory=factory,
        context_builder=context_builder(factory),
        orchestrator=LangGraphAgentOrchestrator(model_gateway=gateway),
        adapter_resolver=resolver,
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=clock or FixedClock(),
        invocation_ids=Generator(lambda: ToolInvocationId(uuid4())),
        policy_evaluation_ids=Generator(lambda: PolicyEvaluationId(uuid4())),
        approval_ids=Generator(lambda: ApprovalRequestId(uuid4())),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
        message_ids=Generator(
            (lambda: MessageId(uuid4())) if message_id_factory is None else message_id_factory
        ),
    )


class FaultySearchAdapter:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationSuccess:
        del request
        self.calls += 1
        if self.mode == "timeout":
            await asyncio.sleep(1)
        return ToolInvocationSuccess({"unexpected": True})

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        raise AssertionError(f"unexpected reconciliation for {idempotency_key}")


async def activate_tool(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    turn_agent_version_id: AgentVersionId,
    team_id: TeamId,
    candidate: ToolManifestCandidate | None = None,
    bind: bool = True,
) -> ToolVersion:
    selected_candidate = search_work_items_manifest() if candidate is None else candidate
    validation = ToolManifestValidator(Draft202012JsonSchemaValidator()).validate(
        selected_candidate
    )
    assert validation.manifest is not None
    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=team_id,
        tool_key=f"tool_{uuid4().hex[:12]}",
        created_at=NOW,
    )
    version_id = ToolVersionId(uuid4())
    run = ToolConformanceRun(
        id=ToolConformanceRunId(uuid4()),
        tool_version_id=version_id,
        status=ToolConformanceStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
    )
    case = ToolConformanceCaseResult(
        id=ToolConformanceCaseResultId(uuid4()),
        run_id=run.id,
        case_key="valid_input_output",
        status=ToolConformanceStatus.PASSED,
        duration_ms=0,
        diagnostic_code=None,
    )
    async with factory() as unit_of_work:
        await unit_of_work.tools.add_definition(definition)
        version = await unit_of_work.tools.add_next_version(
            tool_version_id=version_id,
            tool_definition_id=definition.id,
            manifest=validation.manifest,
            created_at=NOW,
        )
        await unit_of_work.tools.add_conformance_run(run, (case,))
        draft = await unit_of_work.tools.get_version_state(version.id)
        assert draft is not None
        await unit_of_work.tools.update_version_state(
            previous=draft,
            updated=draft.activate(conformance_run_id=run.id, at=NOW),
        )
        if bind:
            await unit_of_work.tools.add_binding(
                AgentToolBinding(
                    id=AgentToolBindingId(uuid4()),
                    agent_version_id=turn_agent_version_id,
                    tool_definition_id=definition.id,
                    tool_version_id=version.id,
                    created_at=NOW,
                )
            )
        await unit_of_work.commit()
    return version


async def test_direct_run_persists_response_and_atomic_terminal_success(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None

    result = await run_turn(
        unit_of_work_factory,
        gateway=ScriptedModelGateway((Respond("Direct answer."),)),
        resolver=StaticToolAdapterResolver({}),
    ).execute(
        RunTurnCommand(
            team_id=conversation.team_id,
            actor_id=ACTOR_ID,
            turn_id=turn.id,
            attempt_id=attempt.id,
            granted_scopes=(),
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=20,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)

    assert stored_turn is not None and stored_turn.status is TurnStatus.COMPLETED
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.SUCCEEDED
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.TURN_COMPLETED,
    ]
    assert (
        "".join(
            str(event.payload["text"])
            for event in events
            if event.kind is ExecutionEventKind.RESPONSE_DELTA
        )
        == "Direct answer."
    )
    assert messages[-1].id == result.assistant_message_id
    assert messages[-1].content == "Direct answer."
    assert result.tool_called is False


async def test_tool_run_orders_tool_progress_before_response_and_completion(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    version = await activate_tool(
        unit_of_work_factory,
        turn_agent_version_id=turn.agent_version_id,
        team_id=conversation.team_id,
    )
    gateway = ScriptedModelGateway(
        (
            CallTool(version.id, {"query": "launch"}),
            Respond("The launch checklist is open."),
        )
    )

    result = await run_turn(
        unit_of_work_factory,
        gateway=gateway,
        resolver=StaticToolAdapterResolver(
            {version.manifest.adapter_key: SearchWorkItemsAdapter()}
        ),
    ).execute(
        RunTurnCommand(
            team_id=conversation.team_id,
            actor_id=ACTOR_ID,
            turn_id=turn.id,
            attempt_id=attempt.id,
            granted_scopes=("work_items:read",),
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=30,
        )
        invocations = await unit_of_work.tool_invocations.list_for_turn(turn.id)

    kinds = [event.kind for event in events]
    assert kinds[:3] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_COMPLETED,
    ]
    assert kinds[-1] is ExecutionEventKind.TURN_COMPLETED
    assert all(
        kinds.index(ExecutionEventKind.TOOL_COMPLETED) < index
        for index, kind in enumerate(kinds)
        if kind is ExecutionEventKind.RESPONSE_DELTA
    )
    assert len(invocations) == 1
    assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
    assert result.tool_called is True


async def test_post_start_orchestration_failure_closes_turn_with_safe_event(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    use_case = run_turn(
        unit_of_work_factory,
        gateway=ScriptedModelGateway((ModelGatewayUnavailableError(),)),
        resolver=StaticToolAdapterResolver({}),
    )

    with pytest.raises(ModelGatewayUnavailableError):
        await use_case.execute(
            RunTurnCommand(
                team_id=conversation.team_id,
                actor_id=ACTOR_ID,
                turn_id=turn.id,
                attempt_id=attempt.id,
                granted_scopes=(),
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=10,
        )

    assert stored_turn is not None and stored_turn.status is TurnStatus.FAILED
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.FAILED
    assert stored_attempt.failure_code == "agent_execution_failed"
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.TURN_FAILED,
    ]
    assert events[-1].payload == {"failure_code": "agent_execution_failed"}


async def test_completion_write_failure_rolls_back_success_before_durable_failure(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    use_case = run_turn(
        unit_of_work_factory,
        gateway=ScriptedModelGateway((Respond("Partial response."),)),
        resolver=StaticToolAdapterResolver({}),
        message_id_factory=lambda: turn.input_message_id,
    )

    with pytest.raises(IntegrityError):
        await use_case.execute(
            RunTurnCommand(
                team_id=conversation.team_id,
                actor_id=ACTOR_ID,
                turn_id=turn.id,
                attempt_id=attempt.id,
                granted_scopes=(),
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=20,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)

    assert stored_turn is not None and stored_turn.status is TurnStatus.FAILED
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.FAILED
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.TURN_FAILED,
    ]
    assert all(event.kind is not ExecutionEventKind.TURN_COMPLETED for event in events)
    assert len(messages) == 1
    assert messages[0].id == turn.input_message_id


async def assert_dispatch_rejected(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    candidate: ToolManifestCandidate,
    bind: bool,
    disable: bool,
    granted_scopes: tuple[str, ...],
    arguments: dict[str, object],
    expected_code: str,
) -> None:
    turn, attempt = await seed_turn(factory, now=NOW)
    async with factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    version = await activate_tool(
        factory,
        turn_agent_version_id=turn.agent_version_id,
        team_id=conversation.team_id,
        candidate=candidate,
        bind=bind,
    )
    if disable:
        async with factory() as unit_of_work:
            state = await unit_of_work.tools.get_version_state(version.id)
            assert state is not None
            await unit_of_work.tools.update_version_state(
                previous=state,
                updated=state.disable(at=NOW),
            )
            await unit_of_work.commit()

    with pytest.raises(ToolDispatchError) as raised:
        await run_turn(
            factory,
            gateway=ScriptedModelGateway((CallTool(version.id, arguments),)),
            resolver=StaticToolAdapterResolver({}),
        ).execute(
            RunTurnCommand(
                team_id=conversation.team_id,
                actor_id=ACTOR_ID,
                turn_id=turn.id,
                attempt_id=attempt.id,
                granted_scopes=granted_scopes,
            )
        )

    async with factory() as unit_of_work:
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=10,
        )
        invocations = await unit_of_work.tool_invocations.list_for_turn(turn.id)
    assert raised.value.failure_code == expected_code
    assert invocations == ()
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.TURN_FAILED,
    ]


async def test_unbound_disabled_unscoped_and_invalid_calls_never_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    await assert_dispatch_rejected(
        unit_of_work_factory,
        candidate=search_work_items_manifest(),
        bind=False,
        disable=False,
        granted_scopes=("work_items:read",),
        arguments={"query": "launch"},
        expected_code="tool_not_eligible",
    )
    await assert_dispatch_rejected(
        unit_of_work_factory,
        candidate=search_work_items_manifest(),
        bind=True,
        disable=True,
        granted_scopes=("work_items:read",),
        arguments={"query": "launch"},
        expected_code="tool_not_eligible",
    )
    await assert_dispatch_rejected(
        unit_of_work_factory,
        candidate=search_work_items_manifest(),
        bind=True,
        disable=False,
        granted_scopes=(),
        arguments={"query": "launch"},
        expected_code="tool_scope_denied",
    )
    await assert_dispatch_rejected(
        unit_of_work_factory,
        candidate=search_work_items_manifest(),
        bind=True,
        disable=False,
        granted_scopes=("work_items:read",),
        arguments={"query": 7},
        expected_code="tool_arguments_invalid",
    )


async def test_mutating_call_durably_pauses_before_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    version = await activate_tool(
        unit_of_work_factory,
        turn_agent_version_id=turn.agent_version_id,
        team_id=conversation.team_id,
        candidate=update_due_date_manifest(),
    )
    gateway = ScriptedModelGateway(
        (
            CallTool(
                version.id,
                {"work_item_id": "WI-1", "due_date": "2026-07-20"},
            ),
        )
    )

    result = await run_turn(
        unit_of_work_factory,
        gateway=gateway,
        resolver=StaticToolAdapterResolver({}),
    ).execute(
        RunTurnCommand(
            team_id=conversation.team_id,
            actor_id=ACTOR_ID,
            turn_id=turn.id,
            attempt_id=attempt.id,
            granted_scopes=version.manifest.required_scopes,
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        invocations = await unit_of_work.tool_invocations.list_for_turn(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=10,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)
        evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(
            invocations[0].id
        )
        approvals = await unit_of_work.approvals.list_requests_for_invocation(invocations[0].id)

    assert result.assistant_message_id is None
    assert result.approval_id == approvals[0].id
    assert result.invocation_id == invocations[0].id
    assert result.chunk_count == 0
    assert len(gateway.requests) == 1
    assert stored_turn is not None
    assert stored_turn.status is TurnStatus.AWAITING_CONFIRMATION
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.AWAITING_CONFIRMATION
    assert len(invocations) == 1
    assert invocations[0].status is ToolInvocationStatus.AWAITING_CONFIRMATION
    assert len(evaluations) == 1
    assert evaluations[0].evaluation.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert evaluations[0].requester_actor_id == ACTOR_ID
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.PENDING
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.APPROVAL_REQUIRED,
    ]
    assert len(messages) == 1
    public_event = repr(events[-1].payload)
    assert "WI-1" not in public_event
    assert "2026-07-20" not in public_event


@pytest.mark.parametrize(
    ("mode", "failure_code"),
    [
        ("timeout", "tool_timeout"),
        ("invalid_output", "tool_output_invalid"),
    ],
)
async def test_tool_failure_preserves_safe_partial_progress_and_fails_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    mode: str,
    failure_code: str,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    candidate = search_work_items_manifest()
    if mode == "timeout":
        candidate = replace(candidate, timeout_ms=1)
    version = await activate_tool(
        unit_of_work_factory,
        turn_agent_version_id=turn.agent_version_id,
        team_id=conversation.team_id,
        candidate=candidate,
    )
    adapter = FaultySearchAdapter(mode)

    with pytest.raises(ToolDispatchError) as raised:
        await run_turn(
            unit_of_work_factory,
            gateway=ScriptedModelGateway((CallTool(version.id, {"query": "launch"}),)),
            resolver=StaticToolAdapterResolver({version.manifest.adapter_key: adapter}),
        ).execute(
            RunTurnCommand(
                team_id=conversation.team_id,
                actor_id=ACTOR_ID,
                turn_id=turn.id,
                attempt_id=attempt.id,
                granted_scopes=("work_items:read",),
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=10,
        )
        invocations = await unit_of_work.tool_invocations.list_for_turn(turn.id)

    assert raised.value.failure_code == failure_code
    assert adapter.calls == 1
    assert stored_turn is not None and stored_turn.status is TurnStatus.FAILED
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.FAILED
    assert len(invocations) == 1
    assert invocations[0].status is ToolInvocationStatus.FAILED
    assert invocations[0].failure_code == failure_code
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_FAILED,
        ExecutionEventKind.TURN_FAILED,
    ]
    assert events[-2].payload == {
        "invocation_id": str(invocations[0].id),
        "failure_code": failure_code,
    }


async def test_only_one_competing_run_turn_can_start_and_complete(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None

    def command() -> RunTurnCommand:
        return RunTurnCommand(
            team_id=conversation.team_id,
            actor_id=ACTOR_ID,
            turn_id=turn.id,
            attempt_id=attempt.id,
            granted_scopes=(),
        )

    results = await asyncio.gather(
        run_turn(
            unit_of_work_factory,
            gateway=ScriptedModelGateway((Respond("First response."),)),
            resolver=StaticToolAdapterResolver({}),
        ).execute(command()),
        run_turn(
            unit_of_work_factory,
            gateway=ScriptedModelGateway((Respond("Second response."),)),
            resolver=StaticToolAdapterResolver({}),
        ).execute(command()),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], (TurnLifecycleConflictError, InvalidStateTransition))

    async with unit_of_work_factory() as unit_of_work:
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=20,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)
    assert sum(event.kind is ExecutionEventKind.TURN_STARTED for event in events) == 1
    assert sum(event.kind is ExecutionEventKind.TURN_COMPLETED for event in events) == 1
    assert len(messages) == 2


async def test_api_created_tool_turn_replays_sse_and_exposes_final_history(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    agent = await seed_agent(unit_of_work_factory, team_id=team_id)
    version = await activate_tool(
        unit_of_work_factory,
        turn_agent_version_id=agent.id,
        team_id=team_id,
    )
    transport = ASGITransport(app=make_app(unit_of_work_factory))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/conversations",
            headers=command_headers(team_id, "day-7-tool-run"),
            json={
                "agent_version_id": str(agent.id),
                "initial_user_message": "Find launch work.",
            },
        )
        assert created.status_code == 202
        accepted = created.json()
        turn_id = TurnId(UUID(accepted["turn_id"]))
        async with unit_of_work_factory() as unit_of_work:
            attempts = await unit_of_work.turns.list_attempts(turn_id)
        assert len(attempts) == 1
        await run_turn(
            unit_of_work_factory,
            gateway=ScriptedModelGateway(
                (
                    CallTool(version.id, {"query": "launch"}),
                    Respond("The launch checklist is open."),
                )
            ),
            resolver=StaticToolAdapterResolver(
                {version.manifest.adapter_key: SearchWorkItemsAdapter()}
            ),
            clock=SystemClock(),
        ).execute(
            RunTurnCommand(
                team_id=team_id,
                actor_id=ACTOR_ID,
                turn_id=turn_id,
                attempt_id=attempts[0].id,
                granted_scopes=("work_items:read",),
            )
        )
        stream = await client.get(
            accepted["events_url"],
            headers={"X-Team-ID": str(team_id)},
        )
        reconnected = await client.get(
            accepted["events_url"],
            headers={"X-Team-ID": str(team_id), "Last-Event-ID": "2"},
        )
        history = await client.get(
            f"{accepted['conversation_url']}/messages",
            headers={"X-Team-ID": str(team_id)},
        )

    assert stream.status_code == 200
    assert stream.headers["content-type"] == "text/event-stream"
    frames = [frame for frame in stream.text.split("\n\n") if frame]
    event_names = [frame.splitlines()[1].removeprefix("event: ") for frame in frames]
    assert event_names[:3] == ["turn.started", "tool.started", "tool.completed"]
    assert event_names[-1] == "turn.completed"
    assert "launch" not in frames[1]
    assert "WI-1" not in frames[2]
    reconnect_frames = [frame for frame in reconnected.text.split("\n\n") if frame]
    assert reconnect_frames[0].splitlines()[0] == "id: 3"
    assert reconnect_frames[0].splitlines()[1] == "event: tool.completed"
    assert history.status_code == 200
    assert history.json()["items"][-1]["content"] == "The launch checklist is open."
