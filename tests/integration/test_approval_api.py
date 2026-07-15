"""Public approval decisions and safe mutation resume."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import (
    ApprovalApiServices,
    build_conversation_api_services,
)
from switchboard.adapters.models.deterministic import ScriptedModelGateway
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import UpdateDueDateAdapter, update_due_date_manifest
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.ports.model_gateway import CallTool
from switchboard.application.ports.tool_adapter import (
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolReconciliationResult,
)
from switchboard.application.services.command_idempotency import hash_idempotency_key
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.application.use_cases.manage_approvals import (
    DecideApprovalCommand,
    ManageApprovals,
)
from switchboard.application.use_cases.run_turn import RunTurnCommand
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.command_receipts import ApprovalDecision, CommandOperation
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    ApprovalRequestId,
    CommandReceiptId,
    ExecutionEventId,
)
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from tests.integration.support import seed_turn
from tests.integration.test_conversation_api import UnexpectedSleeper, make_test_settings
from tests.integration.test_run_turn import ACTOR_ID, NOW, activate_tool, run_turn


class Generator[IdentifierT]:
    def __init__(self, factory: Callable[[], IdentifierT]) -> None:
        self._factory = factory

    def new(self) -> IdentifierT:
        return self._factory()


class DecisionClock:
    def __init__(self, offset: timedelta = timedelta(minutes=1)) -> None:
        self._offset = offset

    def now(self) -> datetime:
        return NOW + self._offset


class CountingDueDateAdapter:
    def __init__(self) -> None:
        self._delegate = UpdateDueDateAdapter()
        self.calls = 0

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls += 1
        return await self._delegate.invoke(request)

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        return await self._delegate.reconcile(idempotency_key)


async def paused_mutation(
    factory: SqlAlchemyUnitOfWorkFactory,
):
    turn, attempt = await seed_turn(factory, now=NOW)
    async with factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    version = await activate_tool(
        factory,
        turn_agent_version_id=turn.agent_version_id,
        team_id=conversation.team_id,
        candidate=update_due_date_manifest(),
    )
    paused = await run_turn(
        factory,
        gateway=ScriptedModelGateway(
            (
                CallTool(
                    version.id,
                    {"work_item_id": "WI-1", "due_date": "2026-07-20"},
                ),
            )
        ),
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
    assert paused.approval_id is not None
    return conversation.team_id, turn, attempt, paused.approval_id, version


def approval_services(
    factory: SqlAlchemyUnitOfWorkFactory,
    adapter: CountingDueDateAdapter,
    *,
    clock: DecisionClock | None = None,
) -> ApprovalApiServices:
    return ApprovalApiServices(
        manage=ManageApprovals(
            unit_of_work_factory=factory,
            adapter_resolver=StaticToolAdapterResolver({"reference.update_due_date.v1": adapter}),
            schema_validator=Draft202012JsonSchemaValidator(),
            clock=clock or DecisionClock(),
            receipt_ids=Generator(lambda: CommandReceiptId(uuid4())),
            event_ids=Generator(lambda: ExecutionEventId(uuid4())),
        )
    )


async def test_safe_read_approve_and_replay_dispatch_exactly_once(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, turn, attempt, approval_id, _ = await paused_mutation(unit_of_work_factory)
    adapter = CountingDueDateAdapter()
    app = create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
        approval_api_services=approval_services(unit_of_work_factory, adapter),
        conversation_api_services=build_conversation_api_services(unit_of_work_factory),
        replay_turn_events=ReplayTurnEvents(
            unit_of_work_factory=unit_of_work_factory,
            sleeper=UnexpectedSleeper(),
        ),
    )
    transport = ASGITransport(app=app)
    decision_actor = ActorId(uuid4())
    headers = {
        "X-Team-ID": str(team_id),
        "X-Actor-ID": str(decision_actor),
        "Idempotency-Key": "approve-due-date-001",
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        read = await client.get(
            f"/api/v1/approvals/{approval_id}",
            headers={"X-Team-ID": str(team_id)},
        )
        cross_team = await client.get(
            f"/api/v1/approvals/{approval_id}",
            headers={"X-Team-ID": str(uuid4())},
        )
        missing_actor = await client.post(
            f"/api/v1/approvals/{approval_id}/decisions",
            headers={
                "X-Team-ID": str(team_id),
                "Idempotency-Key": "missing-actor",
            },
            json={"decision": ApprovalDecision.APPROVE.value},
        )
        approved = await client.post(
            f"/api/v1/approvals/{approval_id}/decisions",
            headers=headers,
            json={"decision": ApprovalDecision.APPROVE.value},
        )
        replay = await client.post(
            f"/api/v1/approvals/{approval_id}/decisions",
            headers=headers,
            json={"decision": ApprovalDecision.APPROVE.value},
        )
        conflicting_replay = await client.post(
            f"/api/v1/approvals/{approval_id}/decisions",
            headers=headers,
            json={"decision": ApprovalDecision.REJECT.value},
        )
        stream = await client.get(
            f"/api/v1/turns/{turn.id}/events",
            headers={"X-Team-ID": str(team_id)},
        )
        history = await client.get(
            f"/api/v1/conversations/{turn.conversation_id}/messages",
            headers={"X-Team-ID": str(team_id)},
        )

    assert read.status_code == 200
    assert read.json()["status"] == ApprovalStatus.PENDING.value
    assert read.json()["target_type"] == "invocation"
    assert read.json()["workflow_id"] is None
    assert read.json()["safe_actions"] == []
    assert read.json()["mutation_count"] is None
    assert cross_team.status_code == 404
    assert missing_actor.status_code == 400
    public_read = read.text
    assert "WI-1" not in public_read
    assert "2026-07-20" not in public_read
    assert "digest" not in public_read
    assert approved.status_code == 200
    assert approved.json()["status"] == ApprovalStatus.CONSUMED.value
    assert approved.json()["invocation_status"] == ToolInvocationStatus.SUCCEEDED.value
    assert approved.json()["workflow_status"] is None
    assert replay.json() == approved.json()
    assert conflicting_replay.status_code == 409
    assert adapter.calls == 1
    assert stream.status_code == 200
    frames = [frame for frame in stream.text.split("\n\n") if frame]
    event_names = [frame.splitlines()[1].removeprefix("event: ") for frame in frames]
    assert event_names == [
        "turn.started",
        "approval.required",
        "approval.resolved",
        "tool.started",
        "tool.completed",
        "turn.completed",
    ]
    public_stream = stream.text
    assert "WI-1" not in public_stream
    assert "2026-07-20" not in public_stream
    assert "digest" not in public_stream
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1

    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(turn_id=turn.id, after_sequence=0, limit=20)
        approval = await unit_of_work.approvals.get_request(approval_id)
        assert approval is not None
        evaluation = await unit_of_work.approvals.get_evaluation(approval.policy_evaluation_id)
        receipt = await unit_of_work.command_receipts.get_by_authority(
            team_id=team_id,
            operation=CommandOperation.DECIDE_APPROVAL,
            command_scope=str(approval_id),
            idempotency_key_hash=hash_idempotency_key("approve-due-date-001"),
        )
    assert stored_turn is not None and stored_turn.status is TurnStatus.COMPLETED
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.SUCCEEDED
    assert approval.requester_actor_id == ACTOR_ID
    assert approval.resolved_by_actor_id == decision_actor
    assert evaluation is not None and evaluation.fingerprint == approval.fingerprint
    assert receipt is not None
    assert receipt.actor_id == decision_actor
    assert receipt.approval_decision is ApprovalDecision.APPROVE
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.APPROVAL_REQUIRED,
        ExecutionEventKind.APPROVAL_RESOLVED,
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_COMPLETED,
        ExecutionEventKind.TURN_COMPLETED,
    ]


async def test_rejection_cancels_without_dispatch_and_replays_stably(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, turn, attempt, approval_id, _ = await paused_mutation(unit_of_work_factory)
    adapter = CountingDueDateAdapter()
    services = approval_services(unit_of_work_factory, adapter)
    actor_id = ActorId(uuid4())
    command = dict(
        team_id=team_id,
        actor_id=actor_id,
        approval_id=ApprovalRequestId(approval_id),
        decision=ApprovalDecision.REJECT,
        idempotency_key="reject-due-date-001",
    )
    rejected = await services.manage.decide(DecideApprovalCommand(**command))
    replay = await services.manage.decide(DecideApprovalCommand(**command))

    assert rejected.approval.status is ApprovalStatus.REJECTED
    assert rejected.invocation_status is ToolInvocationStatus.CANCELLED
    assert replay == rejected
    assert adapter.calls == 0
    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        stored_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(turn_id=turn.id, after_sequence=0, limit=20)
    assert stored_turn is not None and stored_turn.status is TurnStatus.CANCELLED
    assert stored_attempt is not None
    assert stored_attempt.status is TurnAttemptStatus.CANCELLED
    assert [event.kind for event in events][-2:] == [
        ExecutionEventKind.APPROVAL_RESOLVED,
        ExecutionEventKind.TURN_CANCELLED,
    ]


async def test_read_lazily_expires_and_cancels_without_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, turn, _, approval_id, _ = await paused_mutation(unit_of_work_factory)
    adapter = CountingDueDateAdapter()
    services = approval_services(
        unit_of_work_factory,
        adapter,
        clock=DecisionClock(timedelta(minutes=15)),
    )

    expired = await services.manage.get(team_id=team_id, approval_id=approval_id)

    assert expired.status is ApprovalStatus.EXPIRED
    assert adapter.calls == 0
    async with unit_of_work_factory() as unit_of_work:
        stored_turn = await unit_of_work.turns.get(turn.id)
        invocation = await unit_of_work.tool_invocations.get(expired.invocation_id)
    assert stored_turn is not None and stored_turn.status is TurnStatus.CANCELLED
    assert invocation is not None
    assert invocation.status is ToolInvocationStatus.CANCELLED
