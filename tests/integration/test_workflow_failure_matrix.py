from datetime import datetime, timedelta
from uuid import uuid4

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationResult,
)
from switchboard.application.use_cases.cancel_workflow_plan import (
    CancelWorkflowPlan,
    CancelWorkflowPlanCommand,
    WorkflowPlanCancellationReason,
)
from switchboard.application.use_cases.run_approved_workflow import (
    RunApprovedWorkflowCommand,
)
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.identifiers import ActorId, ExecutionEventId
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import WorkflowStatus, WorkflowStepStatus
from tests.integration.test_freeze_workflow_mutation_plan import (
    planner,
    prepared_plan_command,
)
from tests.integration.test_run_approved_workflow import (
    CountingUpdateAdapter,
    approve_plan,
    frozen_approved_workflow,
    workflow_runner,
)
from tests.integration.test_run_workflow_discovery import NOW, FixedClock, Generator


class ClockAt:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


class FailSecondAdapter(CountingUpdateAdapter):
    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        if self.calls == 1:
            self.calls += 1
            return ToolInvocationFailure("declared_failure", retryable=False)
        return await super().invoke(request)


class AmbiguousAdapter(CountingUpdateAdapter):
    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        del request
        self.calls += 1
        raise RuntimeError("simulated connection loss after dispatch")


class TimeoutAdapter(CountingUpdateAdapter):
    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        del request
        self.calls += 1
        raise TimeoutError


async def test_rejection_cancels_every_never_dispatched_action(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}, {"id": "WI-2"}],
    )
    frozen = await planner(unit_of_work_factory).execute(plan_command)
    assert frozen.approval_id is not None

    status = await CancelWorkflowPlan(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    ).execute(
        CancelWorkflowPlanCommand(
            team_id=plan_command.team_id,
            approval_id=frozen.approval_id,
            reason=WorkflowPlanCancellationReason.REJECTED,
            actor_id=ActorId(uuid4()),
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(frozen.workflow_id)
        approval = await unit_of_work.workflow_plan_approvals.get(frozen.approval_id)
        steps = await unit_of_work.workflows.list_steps(frozen.workflow_id)
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
        turn = await unit_of_work.turns.get(plan_command.turn_id)
        attempt = await unit_of_work.turns.get_attempt(plan_command.attempt_id)
    assert status is ApprovalStatus.REJECTED
    assert approval is not None and approval.status is ApprovalStatus.REJECTED
    assert workflow is not None and workflow.status is WorkflowStatus.CANCELLED
    assert [step.status for step in steps[1:]] == [
        WorkflowStepStatus.SKIPPED,
        WorkflowStepStatus.SKIPPED,
        WorkflowStepStatus.SKIPPED,
    ]
    assert [invocation.status for invocation in invocations[1:]] == [
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.CANCELLED,
    ]
    assert turn is not None and turn.status is TurnStatus.CANCELLED
    assert attempt is not None and attempt.status is TurnAttemptStatus.CANCELLED


async def test_expired_approval_cancels_without_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}],
    )
    frozen = await planner(unit_of_work_factory).execute(plan_command)
    assert frozen.approval_id is not None

    status = await CancelWorkflowPlan(
        unit_of_work_factory=unit_of_work_factory,
        clock=ClockAt(NOW + timedelta(minutes=16)),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    ).execute(
        CancelWorkflowPlanCommand(
            team_id=plan_command.team_id,
            approval_id=frozen.approval_id,
            reason=WorkflowPlanCancellationReason.EXPIRED,
        )
    )

    assert status is ApprovalStatus.EXPIRED
    async with unit_of_work_factory() as unit_of_work:
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
    assert invocations[1].status is ToolInvocationStatus.CANCELLED


async def test_known_failure_stops_later_mutations_and_finishes_truthfully(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}, {"id": "WI-2"}, {"id": "WI-3"}],
    )
    frozen = await planner(unit_of_work_factory).execute(plan_command)
    assert frozen.approval_id is not None
    await approve_plan(
        unit_of_work_factory,
        team_id=plan_command.team_id,
        approval_id=frozen.approval_id,
    )
    adapter = FailSecondAdapter()

    result = await workflow_runner(unit_of_work_factory, adapter).execute(
        RunApprovedWorkflowCommand(
            team_id=plan_command.team_id,
            workflow_id=frozen.workflow_id,
        )
    )

    assert result.response_text == "Workflow stopped: 1 succeeded, 1 failed, 1 skipped."
    assert adapter.calls == 2
    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(frozen.workflow_id)
        steps = await unit_of_work.workflows.list_steps(frozen.workflow_id)
        turn = await unit_of_work.turns.get(plan_command.turn_id)
    assert workflow is not None and workflow.status is WorkflowStatus.FAILED
    assert [step.status for step in steps[1:4]] == [
        WorkflowStepStatus.SUCCEEDED,
        WorkflowStepStatus.FAILED,
        WorkflowStepStatus.SKIPPED,
    ]
    assert turn is not None and turn.status is TurnStatus.FAILED


async def test_ambiguous_dispatch_becomes_unknown_and_is_never_retried(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = AmbiguousAdapter()
    command = RunApprovedWorkflowCommand(
        team_id=plan_command.team_id,
        workflow_id=frozen.workflow_id,
    )

    result = await workflow_runner(unit_of_work_factory, adapter).execute(command)
    replay = await workflow_runner(unit_of_work_factory, adapter).execute(command)

    assert result.response_text == (
        "Workflow requires review: 0 succeeded, 1 outcome unknown, 1 skipped."
    )
    assert replay.replayed is True
    assert adapter.calls == 1
    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(frozen.workflow_id)
        steps = await unit_of_work.workflows.list_steps(frozen.workflow_id)
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
    assert workflow is not None and workflow.status is WorkflowStatus.REVIEW_REQUIRED
    assert [step.status for step in steps[1:3]] == [
        WorkflowStepStatus.UNKNOWN,
        WorkflowStepStatus.SKIPPED,
    ]
    assert [invocation.status for invocation in invocations[1:]] == [
        ToolInvocationStatus.UNKNOWN,
        ToolInvocationStatus.CANCELLED,
    ]


async def test_explicit_crash_recovery_marks_running_unknown_without_adapter_call(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = CountingUpdateAdapter()
    normal_command = RunApprovedWorkflowCommand(
        team_id=plan_command.team_id,
        workflow_id=frozen.workflow_id,
    )
    interrupted = workflow_runner(unit_of_work_factory, adapter)
    assert await interrupted._resume(normal_command) is None
    claimed = await interrupted._claim_next(normal_command)
    assert claimed is not None

    recovered = await workflow_runner(unit_of_work_factory, adapter).execute(
        RunApprovedWorkflowCommand(
            team_id=plan_command.team_id,
            workflow_id=frozen.workflow_id,
            recover_running_as_unknown=True,
        )
    )

    assert recovered.response_text == (
        "Workflow requires review: 0 succeeded, 1 outcome unknown, 1 skipped."
    )
    assert adapter.calls == 0
    async with unit_of_work_factory() as unit_of_work:
        invocation = await unit_of_work.tool_invocations.get(claimed.invocation.id)
    assert invocation is not None and invocation.status is ToolInvocationStatus.UNKNOWN


async def test_timeout_is_unknown_and_does_not_retry(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = TimeoutAdapter()

    result = await workflow_runner(unit_of_work_factory, adapter).execute(
        RunApprovedWorkflowCommand(
            team_id=plan_command.team_id,
            workflow_id=frozen.workflow_id,
        )
    )

    assert result.response_text.startswith("Workflow requires review")
    assert adapter.calls == 1
    async with unit_of_work_factory() as unit_of_work:
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
    assert invocations[1].status is ToolInvocationStatus.UNKNOWN
    assert invocations[1].failure_code == "tool_timeout"


async def test_recreated_runner_finalizes_after_last_committed_tool_result(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = CountingUpdateAdapter()
    command = RunApprovedWorkflowCommand(
        team_id=plan_command.team_id,
        workflow_id=frozen.workflow_id,
    )
    interrupted = workflow_runner(unit_of_work_factory, adapter)
    assert await interrupted._resume(command) is None
    for _ in range(2):
        claimed = await interrupted._claim_next(command)
        assert claimed is not None
        await interrupted._dispatch_and_record(claimed)

    completed = await workflow_runner(unit_of_work_factory, adapter).execute(command)

    assert completed.response_text == "Workflow completed: 2 planned updates succeeded."
    assert adapter.calls == 2


async def test_disabled_tool_is_rejected_before_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    async with unit_of_work_factory() as unit_of_work:
        state = await unit_of_work.tools.get_version_state(plan_command.mutation_tool_version_id)
        assert state is not None
        await unit_of_work.tools.update_version_state(
            previous=state,
            updated=state.disable(at=NOW),
        )
        await unit_of_work.commit()
    adapter = CountingUpdateAdapter()

    result = await workflow_runner(unit_of_work_factory, adapter).execute(
        RunApprovedWorkflowCommand(
            team_id=plan_command.team_id,
            workflow_id=frozen.workflow_id,
        )
    )

    assert result.response_text == "Workflow stopped before dispatch: 0 succeeded, 2 skipped."
    assert adapter.calls == 0
