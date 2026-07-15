"""Deterministic approval-gated multi-tool Phase 1 demo journey."""

import json
from dataclasses import dataclass
from time import perf_counter
from typing import cast
from uuid import UUID

from httpx import ASGITransport, AsyncClient, Response

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import (
    ApprovalApiServices,
    build_approval_api_services,
)
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.streaming.asyncio_sleeper import AsyncioSleeper
from switchboard.adapters.tools.reference import SearchWorkItemsAdapter, UpdateDueDateAdapter
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.ports.tool_adapter import ToolInvocationRequest, ToolInvocationResult
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.application.use_cases.approve_workflow_plan import ApproveWorkflowPlan
from switchboard.application.use_cases.cancel_workflow_plan import CancelWorkflowPlan
from switchboard.application.use_cases.freeze_workflow_mutation_plan import (
    FreezeWorkflowMutationPlan,
    FreezeWorkflowMutationPlanCommand,
)
from switchboard.application.use_cases.manage_workflow_plan_approvals import (
    ManageWorkflowPlanApprovals,
)
from switchboard.application.use_cases.run_approved_workflow import (
    RunApprovedWorkflow,
    RunApprovedWorkflowCommand,
)
from switchboard.application.use_cases.run_workflow_discovery import (
    RunWorkflowDiscovery,
    RunWorkflowDiscoveryCommand,
)
from switchboard.bootstrap.config import Settings
from switchboard.bootstrap.demo import (
    READ_ONLY_CONVERSATION_ID,
    WORKFLOW_ATTEMPT_ID,
    WORKFLOW_TURN_ID,
    DemoJourneyError,
    SseFrame,
    StageTiming,
    demo_conversation_services,
    parse_sse,
)
from switchboard.bootstrap.demo_environment import (
    DEMO_ACTOR_ID,
    DEMO_AGENT_VERSION_ID,
    DEMO_SEARCH_VERSION_ID,
    DEMO_TEAM_ID,
    DEMO_UPDATE_VERSION_ID,
    FixedClock,
    SequenceIdGenerator,
)
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ExecutionEventId,
    MessageId,
    PolicyEvaluationId,
    ToolInvocationId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import WorkflowStatus, WorkflowStepStatus

WORKFLOW_PROMPT = "Find overdue critical tasks, move them to Friday, and summarize the changes."
WORKFLOW_TARGET_DUE_DATE = "2026-07-17"
WORKFLOW_RESPONSE = "Workflow completed: 2 planned updates succeeded."
WORKFLOW_CONTINUE_KEY = "phase-1-approval-workflow-v1"
WORKFLOW_APPROVAL_KEY = "phase-1-plan-approval-v1"

DEMO_WORKFLOW_ID = TurnWorkflowId(UUID("a0000000-0000-4000-8000-000000000001"))
DEMO_APPROVAL_ID = WorkflowPlanApprovalId(UUID("a0000000-0000-4000-8000-000000000002"))
DEMO_WORKFLOW_OUTPUT_MESSAGE_ID = MessageId(UUID("a0000000-0000-4000-8000-000000000003"))
DEMO_WORKFLOW_STEP_IDS = tuple(
    WorkflowStepId(UUID(f"a1000000-0000-4000-8000-{index:012d}")) for index in range(1, 5)
)
DEMO_WORKFLOW_INVOCATION_IDS = tuple(
    ToolInvocationId(UUID(f"a2000000-0000-4000-8000-{index:012d}")) for index in range(1, 4)
)
DEMO_WORKFLOW_POLICY_IDS = tuple(
    PolicyEvaluationId(UUID(f"a3000000-0000-4000-8000-{index:012d}")) for index in range(1, 4)
)
DEMO_WORKFLOW_EVENT_IDS = tuple(
    ExecutionEventId(UUID(f"a4000000-0000-4000-8000-{index:012d}")) for index in range(1, 15)
)


@dataclass(frozen=True, slots=True)
class ApprovalWorkflowJourneyResult:
    workflow_id: TurnWorkflowId
    approval_id: WorkflowPlanApprovalId
    discovery_calls: int
    mutation_calls: int
    duplicate_resume_calls: int
    duplicate_resume_replayed: bool
    response_text: str
    event_names: tuple[str, ...]
    history_sequences: tuple[int, ...]
    runner_recreated: bool
    measurement_environment: str
    stage_timings: tuple[StageTiming, ...]

    def to_safe_output(self) -> dict[str, object]:
        return {
            "journey": "approval-workflow",
            "workflow_id": str(self.workflow_id),
            "approval_id": str(self.approval_id),
            "discovery_calls": self.discovery_calls,
            "mutation_calls": self.mutation_calls,
            "duplicate_resume_calls": self.duplicate_resume_calls,
            "duplicate_resume_replayed": self.duplicate_resume_replayed,
            "runner_recreated": self.runner_recreated,
            "response": self.response_text,
            "events": self.event_names,
            "history_sequences": self.history_sequences,
            "measurement": {
                "environment": self.measurement_environment,
                "sample_size": 1,
                "scope": "single deterministic development journey",
                "production_capacity_claim": False,
                "stage_timings_ms": {
                    timing.stage: timing.milliseconds for timing in self.stage_timings
                },
            },
        }


class CountingSearchAdapter(SearchWorkItemsAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls += 1
        return await super().invoke(request)


class CountingUpdateAdapter(UpdateDueDateAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls += 1
        return await super().invoke(request)


async def run_approval_workflow_journey(
    initial_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    recreated_factory: SqlAlchemyUnitOfWorkFactory,
    settings: Settings,
) -> ApprovalWorkflowJourneyResult:
    """Continue, pause, approve, recreate, resume, and audit one frozen plan."""

    async with initial_factory() as unit_of_work:
        existing_turn = await unit_of_work.turns.get(WORKFLOW_TURN_ID)
    if existing_turn is not None:
        raise DemoJourneyError(
            "approval workflow already exists; run reset and both journeys again"
        )
    await _require_read_only_prerequisite(initial_factory)

    app = create_app(
        settings=settings,
        readiness_service=ReadinessService(probes=()),
        replay_turn_events=ReplayTurnEvents(
            unit_of_work_factory=initial_factory,
            sleeper=AsyncioSleeper(),
        ),
        conversation_api_services=demo_conversation_services(initial_factory),
        approval_api_services=_approval_services(initial_factory),
    )
    transport = ASGITransport(app=app)
    timings: list[StageTiming] = []
    total_started = perf_counter()

    async with AsyncClient(transport=transport, base_url="http://switchboard.demo") as client:
        started = perf_counter()
        continued = await client.post(
            f"/api/v1/conversations/{READ_ONLY_CONVERSATION_ID}/turns",
            headers={
                "X-Team-ID": str(DEMO_TEAM_ID),
                "Idempotency-Key": WORKFLOW_CONTINUE_KEY,
            },
            json={"user_message": WORKFLOW_PROMPT},
        )
        timings.append(_timing("api_continuation", started))
        _require_status(continued, 202, "conversation continuation")
        continued_body = _json_object(continued)
        if continued_body.get("turn_id") != str(WORKFLOW_TURN_ID):
            raise DemoJourneyError("continuation returned an unexpected turn")

        await _start_workflow_execution(initial_factory)

        discovery_adapter = CountingSearchAdapter()
        discovery_command = RunWorkflowDiscoveryCommand(
            team_id=DEMO_TEAM_ID,
            actor_id=DEMO_ACTOR_ID,
            agent_version_id=DEMO_AGENT_VERSION_ID,
            turn_id=WORKFLOW_TURN_ID,
            attempt_id=WORKFLOW_ATTEMPT_ID,
            tool_version_id=DEMO_SEARCH_VERSION_ID,
            arguments={"query": "WI-", "limit": 2},
            granted_scopes=("work_items:read",),
        )
        started = perf_counter()
        discovery = _discovery_runner(initial_factory, discovery_adapter)
        discovered = await discovery.execute(discovery_command)
        replayed_discovery = await _discovery_runner(
            initial_factory,
            discovery_adapter,
        ).execute(discovery_command)
        timings.append(_timing("discovery_and_replay", started))
        if discovered.replayed or not replayed_discovery.replayed or discovery_adapter.calls != 1:
            raise DemoJourneyError("discovery replay did not preserve one adapter call")

        started = perf_counter()
        frozen = await _planner(initial_factory).execute(
            FreezeWorkflowMutationPlanCommand(
                team_id=DEMO_TEAM_ID,
                actor_id=DEMO_ACTOR_ID,
                agent_version_id=DEMO_AGENT_VERSION_ID,
                turn_id=WORKFLOW_TURN_ID,
                attempt_id=WORKFLOW_ATTEMPT_ID,
                mutation_tool_version_id=DEMO_UPDATE_VERSION_ID,
                target_due_date=WORKFLOW_TARGET_DUE_DATE,
                granted_scopes=("work_items:read", "work_items:write"),
            )
        )
        timings.append(_timing("plan_freeze", started))
        if (
            frozen.workflow_id != DEMO_WORKFLOW_ID
            or frozen.approval_id != DEMO_APPROVAL_ID
            or frozen.mutation_count != 2
            or not frozen.awaiting_confirmation
        ):
            raise DemoJourneyError("frozen workflow plan did not match the bounded journey")

        started = perf_counter()
        pending = await client.get(
            f"/api/v1/approvals/{DEMO_APPROVAL_ID}",
            headers={"X-Team-ID": str(DEMO_TEAM_ID)},
        )
        _require_status(pending, 200, "pending plan approval")
        _verify_safe_pending_approval(pending)
        approved = await client.post(
            f"/api/v1/approvals/{DEMO_APPROVAL_ID}/decisions",
            headers={
                "X-Team-ID": str(DEMO_TEAM_ID),
                "X-Actor-ID": str(DEMO_ACTOR_ID),
                "Idempotency-Key": WORKFLOW_APPROVAL_KEY,
            },
            json={"decision": "approve"},
        )
        timings.append(_timing("public_approval", started))
        _require_status(approved, 200, "plan approval")
        if _json_object(approved).get("status") != ApprovalStatus.APPROVED.value:
            raise DemoJourneyError("public approval did not record the approved decision")

        del discovery
        mutation_adapter = CountingUpdateAdapter()
        started = perf_counter()
        completed = await _workflow_runner(recreated_factory, mutation_adapter).execute(
            RunApprovedWorkflowCommand(
                team_id=DEMO_TEAM_ID,
                workflow_id=DEMO_WORKFLOW_ID,
            )
        )
        timings.append(_timing("recreated_runner_resume", started))

        duplicate_adapter = CountingUpdateAdapter()
        started = perf_counter()
        duplicate = await _workflow_runner(recreated_factory, duplicate_adapter).execute(
            RunApprovedWorkflowCommand(
                team_id=DEMO_TEAM_ID,
                workflow_id=DEMO_WORKFLOW_ID,
            )
        )
        timings.append(_timing("duplicate_resume", started))
        if (
            completed.replayed
            or not duplicate.replayed
            or duplicate.output_message_id != completed.output_message_id
            or completed.response_text != WORKFLOW_RESPONSE
            or mutation_adapter.calls != 2
            or duplicate_adapter.calls != 0
        ):
            raise DemoJourneyError(
                "resume replay did not preserve exactly one logical mutation set"
            )

        started = perf_counter()
        final_approval = await client.get(
            f"/api/v1/approvals/{DEMO_APPROVAL_ID}",
            headers={"X-Team-ID": str(DEMO_TEAM_ID)},
        )
        stream = await client.get(
            f"/api/v1/turns/{WORKFLOW_TURN_ID}/events",
            headers={"X-Team-ID": str(DEMO_TEAM_ID)},
        )
        history = await client.get(
            f"/api/v1/conversations/{READ_ONLY_CONVERSATION_ID}/messages",
            headers={"X-Team-ID": str(DEMO_TEAM_ID)},
        )
        timings.append(_timing("final_public_evidence", started))
        _require_status(final_approval, 200, "consumed plan approval")
        if _json_object(final_approval).get("status") != ApprovalStatus.CONSUMED.value:
            raise DemoJourneyError("final public approval is not consumed")
        _require_status(stream, 200, "workflow event stream")
        event_frames = parse_sse(stream.text)
        _verify_safe_workflow_stream(stream, event_frames)
        _require_status(history, 200, "multi-turn history")
        history_sequences = _verify_history(_json_object(history))

    await _verify_durable_audit(recreated_factory)
    timings.append(_timing("total", total_started))
    return ApprovalWorkflowJourneyResult(
        workflow_id=DEMO_WORKFLOW_ID,
        approval_id=DEMO_APPROVAL_ID,
        discovery_calls=discovery_adapter.calls,
        mutation_calls=mutation_adapter.calls,
        duplicate_resume_calls=duplicate_adapter.calls,
        duplicate_resume_replayed=duplicate.replayed,
        response_text=completed.response_text,
        event_names=tuple(frame.event for frame in event_frames),
        history_sequences=history_sequences,
        runner_recreated=initial_factory is not recreated_factory,
        measurement_environment=settings.environment,
        stage_timings=tuple(timings),
    )


async def _require_read_only_prerequisite(factory: SqlAlchemyUnitOfWorkFactory) -> None:
    from switchboard.bootstrap.demo import READ_ONLY_TURN_ID

    async with factory() as unit_of_work:
        turn = await unit_of_work.turns.get(READ_ONLY_TURN_ID)
    if turn is None or turn.status is not TurnStatus.COMPLETED:
        raise DemoJourneyError("run the read-only journey before the approval workflow")


async def _start_workflow_execution(factory: SqlAlchemyUnitOfWorkFactory) -> None:
    at = FixedClock().now()
    async with factory() as unit_of_work:
        turn = await unit_of_work.turns.get_for_update(WORKFLOW_TURN_ID)
        attempt = await unit_of_work.turns.get_attempt(WORKFLOW_ATTEMPT_ID)
        if (
            turn is None
            or attempt is None
            or turn.status is not TurnStatus.RECEIVED
            or attempt.status is not TurnAttemptStatus.PENDING
        ):
            raise DemoJourneyError("accepted workflow turn is not claimable")
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
        if conversation is None or conversation.team_id != DEMO_TEAM_ID:
            raise DemoJourneyError("accepted workflow turn has invalid ownership")
        await unit_of_work.turns.update_turn_lifecycle(previous=turn, updated=turn.start())
        await unit_of_work.turns.update_attempt_lifecycle(
            previous=attempt,
            updated=attempt.start(at=at),
        )
        await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=DEMO_WORKFLOW_EVENT_IDS[0],
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={"attempt_number": attempt.attempt_number},
            occurred_at=at,
        )
        await unit_of_work.commit()


def _discovery_runner(
    factory: SqlAlchemyUnitOfWorkFactory,
    adapter: CountingSearchAdapter,
) -> RunWorkflowDiscovery:
    return RunWorkflowDiscovery(
        unit_of_work_factory=factory,
        adapter_resolver=StaticToolAdapterResolver({"reference.search_work_items.v1": adapter}),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        workflow_ids=SequenceIdGenerator((DEMO_WORKFLOW_ID,)),
        step_ids=SequenceIdGenerator((DEMO_WORKFLOW_STEP_IDS[0],)),
        invocation_ids=SequenceIdGenerator((DEMO_WORKFLOW_INVOCATION_IDS[0],)),
        policy_evaluation_ids=SequenceIdGenerator((DEMO_WORKFLOW_POLICY_IDS[0],)),
        event_ids=SequenceIdGenerator(DEMO_WORKFLOW_EVENT_IDS[1:3]),
    )


def _planner(factory: SqlAlchemyUnitOfWorkFactory) -> FreezeWorkflowMutationPlan:
    return FreezeWorkflowMutationPlan(
        unit_of_work_factory=factory,
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        invocation_ids=SequenceIdGenerator(DEMO_WORKFLOW_INVOCATION_IDS[1:]),
        policy_evaluation_ids=SequenceIdGenerator(DEMO_WORKFLOW_POLICY_IDS[1:]),
        approval_ids=SequenceIdGenerator((DEMO_APPROVAL_ID,)),
        step_ids=SequenceIdGenerator(DEMO_WORKFLOW_STEP_IDS[1:]),
        event_ids=SequenceIdGenerator(DEMO_WORKFLOW_EVENT_IDS[3:5]),
    )


def _approval_services(factory: SqlAlchemyUnitOfWorkFactory) -> ApprovalApiServices:
    schema_validator = Draft202012JsonSchemaValidator()
    base = build_approval_api_services(
        factory,
        adapter_resolver=StaticToolAdapterResolver(
            {"reference.update_due_date.v1": UpdateDueDateAdapter()}
        ),
        schema_validator=schema_validator,
    )
    clock = FixedClock()
    return ApprovalApiServices(
        manage=base.manage,
        workflow_plans=ManageWorkflowPlanApprovals(
            unit_of_work_factory=factory,
            clock=clock,
            approve=ApproveWorkflowPlan(
                unit_of_work_factory=factory,
                clock=clock,
                event_ids=SequenceIdGenerator((DEMO_WORKFLOW_EVENT_IDS[5],)),
            ),
            cancel=CancelWorkflowPlan(
                unit_of_work_factory=factory,
                clock=clock,
                event_ids=SequenceIdGenerator((DEMO_WORKFLOW_EVENT_IDS[13],)),
            ),
        ),
    )


def _workflow_runner(
    factory: SqlAlchemyUnitOfWorkFactory,
    adapter: CountingUpdateAdapter,
) -> RunApprovedWorkflow:
    return RunApprovedWorkflow(
        unit_of_work_factory=factory,
        adapter_resolver=StaticToolAdapterResolver({"reference.update_due_date.v1": adapter}),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        message_ids=SequenceIdGenerator((DEMO_WORKFLOW_OUTPUT_MESSAGE_ID,)),
        event_ids=SequenceIdGenerator(DEMO_WORKFLOW_EVENT_IDS[6:13]),
    )


async def _verify_durable_audit(factory: SqlAlchemyUnitOfWorkFactory) -> None:
    async with factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(DEMO_WORKFLOW_ID)
        approval = await unit_of_work.workflow_plan_approvals.get(DEMO_APPROVAL_ID)
        steps = await unit_of_work.workflows.list_steps(DEMO_WORKFLOW_ID)
        invocations = await unit_of_work.tool_invocations.list_for_turn(WORKFLOW_TURN_ID)
        turn = await unit_of_work.turns.get(WORKFLOW_TURN_ID)
        attempt = await unit_of_work.turns.get_attempt(WORKFLOW_ATTEMPT_ID)
        evidence_counts: list[int] = []
        for invocation in invocations:
            evidence = await unit_of_work.approvals.list_evaluations_for_invocation(invocation.id)
            evidence_counts.append(len(evidence))
    if (
        workflow is None
        or workflow.status is not WorkflowStatus.COMPLETED
        or approval is None
        or approval.status is not ApprovalStatus.CONSUMED
        or len(steps) != 4
        or any(step.status is not WorkflowStepStatus.SUCCEEDED for step in steps)
        or len(invocations) != 3
        or any(
            invocation.status is not ToolInvocationStatus.SUCCEEDED for invocation in invocations
        )
        or len({invocation.idempotency_key for invocation in invocations}) != 3
        or evidence_counts != [1, 1, 1]
        or turn is None
        or turn.status is not TurnStatus.COMPLETED
        or attempt is None
        or attempt.status is not TurnAttemptStatus.SUCCEEDED
    ):
        raise DemoJourneyError("durable workflow audit evidence is incomplete")


def _verify_safe_pending_approval(response: Response) -> None:
    body = _json_object(response)
    actions = body.get("safe_actions")
    if (
        body.get("target_type") != "workflow_plan"
        or body.get("mutation_count") != 2
        or not isinstance(actions, list)
        or [action.get("step_number") for action in actions if isinstance(action, dict)] != [2, 3]
    ):
        raise DemoJourneyError("public pending approval did not expose the bounded safe plan")
    if any(value in response.text for value in ("WI-1", WORKFLOW_TARGET_DUE_DATE, "digest")):
        raise DemoJourneyError("public pending approval exposed executable values")


def _verify_safe_workflow_stream(response: Response, frames: tuple[SseFrame, ...]) -> None:
    event_names = tuple(frame.event for frame in frames)
    required = {"workflow.planned", "workflow.resumed", "workflow.terminal", "turn.completed"}
    if not required.issubset(event_names):
        raise DemoJourneyError("workflow stream is missing required progress events")
    if any(value in response.text for value in ("WI-1", WORKFLOW_TARGET_DUE_DATE, "digest")):
        raise DemoJourneyError("workflow stream exposed executable values")


def _verify_history(body: dict[str, object]) -> tuple[int, ...]:
    items = body.get("items")
    if not isinstance(items, list) or len(items) != 4:
        raise DemoJourneyError("multi-turn history did not contain four messages")
    sequences = tuple(item.get("sequence") for item in items if isinstance(item, dict))
    contents = tuple(item.get("content") for item in items if isinstance(item, dict))
    if sequences != (1, 2, 3, 4) or contents[-2:] != (WORKFLOW_PROMPT, WORKFLOW_RESPONSE):
        raise DemoJourneyError("multi-turn history is not ordered or complete")
    return cast(tuple[int, ...], sequences)


def _json_object(response: Response) -> dict[str, object]:
    value = json.loads(response.text)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DemoJourneyError("API returned a non-object response")
    return cast(dict[str, object], value)


def _require_status(response: Response, expected: int, stage: str) -> None:
    if response.status_code != expected:
        raise DemoJourneyError(f"{stage} returned HTTP {response.status_code}")


def _timing(stage: str, started: float) -> StageTiming:
    return StageTiming(stage, round((perf_counter() - started) * 1000, 3))
