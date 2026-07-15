import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import update

from switchboard.adapters.persistence.schema import tool_invocations
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import UpdateDueDateAdapter
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.errors import (
    WorkflowExecutionConflictError,
    WorkflowExecutionInProgressError,
)
from switchboard.application.ports.tool_adapter import (
    ToolInvocationRequest,
    ToolInvocationResult,
)
from switchboard.application.use_cases.approve_workflow_plan import (
    ApproveWorkflowPlan,
    ApproveWorkflowPlanCommand,
)
from switchboard.application.use_cases.run_approved_workflow import (
    RunApprovedWorkflow,
    RunApprovedWorkflowCommand,
)
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import ExecutionEventId, MessageId
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import (
    WorkflowStatus,
    WorkflowStepKind,
    WorkflowStepStatus,
)
from tests.integration.test_freeze_workflow_mutation_plan import (
    planner,
    prepared_plan_command,
)
from tests.integration.test_run_workflow_discovery import ACTOR_ID, FixedClock, Generator

ADAPTER_KEY = "reference.update_due_date.v1"


class CountingUpdateAdapter(UpdateDueDateAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls += 1
        return await super().invoke(request)


class BlockingFirstUpdateAdapter(CountingUpdateAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        if self.calls == 0:
            self.started.set()
            await self.release.wait()
        return await super().invoke(request)


def workflow_runner(
    factory: SqlAlchemyUnitOfWorkFactory,
    adapter: CountingUpdateAdapter,
) -> RunApprovedWorkflow:
    return RunApprovedWorkflow(
        unit_of_work_factory=factory,
        adapter_resolver=StaticToolAdapterResolver({ADAPTER_KEY: adapter}),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        message_ids=Generator(lambda: MessageId(uuid4())),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    )


async def approve_plan(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id,
    approval_id,
) -> None:
    status = await ApproveWorkflowPlan(
        unit_of_work_factory=factory,
        clock=FixedClock(),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    ).execute(
        ApproveWorkflowPlanCommand(
            team_id=team_id,
            actor_id=ACTOR_ID,
            approval_id=approval_id,
        )
    )
    assert status is ApprovalStatus.APPROVED


async def frozen_approved_workflow(factory: SqlAlchemyUnitOfWorkFactory):
    plan_command = await prepared_plan_command(
        factory,
        items=[{"id": "WI-1"}, {"id": "WI-2"}],
    )
    frozen = await planner(factory).execute(plan_command)
    assert frozen.approval_id is not None
    await approve_plan(
        factory,
        team_id=plan_command.team_id,
        approval_id=frozen.approval_id,
    )
    return plan_command, frozen


async def test_recreated_runner_executes_stable_keys_once_and_replays_final_result(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = CountingUpdateAdapter()
    command = RunApprovedWorkflowCommand(
        team_id=plan_command.team_id,
        workflow_id=frozen.workflow_id,
    )

    completed = await workflow_runner(unit_of_work_factory, adapter).execute(command)
    replay = await workflow_runner(unit_of_work_factory, adapter).execute(command)

    assert completed.replayed is False
    assert replay.replayed is True
    assert replay.output_message_id == completed.output_message_id
    assert replay.response_text == "Workflow completed: 2 planned updates succeeded."
    assert adapter.calls == 2
    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(frozen.workflow_id)
        approval = await unit_of_work.workflow_plan_approvals.get_for_workflow(frozen.workflow_id)
        steps = await unit_of_work.workflows.list_steps(frozen.workflow_id)
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
        turn = await unit_of_work.turns.get(plan_command.turn_id)
        attempt = await unit_of_work.turns.get_attempt(plan_command.attempt_id)
        events = await unit_of_work.turns.list_events(
            turn_id=plan_command.turn_id,
            after_sequence=0,
            limit=30,
        )
    assert workflow is not None and workflow.status is WorkflowStatus.COMPLETED
    assert approval is not None and approval.status is ApprovalStatus.CONSUMED
    assert [step.status for step in steps] == [
        WorkflowStepStatus.SUCCEEDED,
        WorkflowStepStatus.SUCCEEDED,
        WorkflowStepStatus.SUCCEEDED,
        WorkflowStepStatus.SUCCEEDED,
    ]
    assert all(invocation.status is ToolInvocationStatus.SUCCEEDED for invocation in invocations)
    assert turn is not None and turn.status is TurnStatus.COMPLETED
    assert attempt is not None and attempt.status is TurnAttemptStatus.SUCCEEDED
    assert [event.kind for event in events].count(ExecutionEventKind.TOOL_STARTED) == 3
    assert [event.kind for event in events].count(ExecutionEventKind.TOOL_COMPLETED) == 3
    assert [event.kind for event in events].count(ExecutionEventKind.TURN_COMPLETED) == 1


async def test_new_runner_skips_a_committed_first_mutation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = CountingUpdateAdapter()
    command = RunApprovedWorkflowCommand(
        team_id=plan_command.team_id,
        workflow_id=frozen.workflow_id,
    )
    first_runner = workflow_runner(unit_of_work_factory, adapter)

    assert await first_runner._resume(command) is None
    first = await first_runner._claim_next(command)
    assert first is not None
    first_key = first.invocation.idempotency_key
    await first_runner._dispatch_and_record(first)
    completed = await workflow_runner(unit_of_work_factory, adapter).execute(command)

    assert completed.mutation_count == 2
    assert adapter.calls == 2
    async with unit_of_work_factory() as unit_of_work:
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
    assert invocations[1].idempotency_key == first_key
    assert invocations[1].status is ToolInvocationStatus.SUCCEEDED


async def test_concurrent_runner_cannot_claim_the_same_mutation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    adapter = BlockingFirstUpdateAdapter()
    command = RunApprovedWorkflowCommand(
        team_id=plan_command.team_id,
        workflow_id=frozen.workflow_id,
    )
    first = asyncio.create_task(workflow_runner(unit_of_work_factory, adapter).execute(command))
    await adapter.started.wait()

    with pytest.raises(WorkflowExecutionInProgressError):
        await workflow_runner(unit_of_work_factory, adapter).execute(command)
    adapter.release.set()
    completed = await first

    assert completed.mutation_count == 2
    assert adapter.calls == 2


async def test_changed_frozen_arguments_are_rejected_before_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command, frozen = await frozen_approved_workflow(unit_of_work_factory)
    async with unit_of_work_factory() as unit_of_work:
        invocations = await unit_of_work.tool_invocations.list_for_turn(plan_command.turn_id)
        await unit_of_work._session.execute(
            update(tool_invocations)
            .where(tool_invocations.c.id == invocations[1].id)
            .values(arguments={"work_item_id": "WI-1", "due_date": "2026-07-18"})
        )
        await unit_of_work.commit()
    adapter = CountingUpdateAdapter()

    with pytest.raises(WorkflowExecutionConflictError, match="evidence"):
        await workflow_runner(unit_of_work_factory, adapter).execute(
            RunApprovedWorkflowCommand(
                team_id=plan_command.team_id,
                workflow_id=frozen.workflow_id,
            )
        )

    assert adapter.calls == 0


async def test_zero_mutation_workflow_finalizes_without_approval(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    plan_command = await prepared_plan_command(unit_of_work_factory, items=[])
    frozen = await planner(unit_of_work_factory).execute(plan_command)
    adapter = CountingUpdateAdapter()

    completed = await workflow_runner(unit_of_work_factory, adapter).execute(
        RunApprovedWorkflowCommand(
            team_id=plan_command.team_id,
            workflow_id=frozen.workflow_id,
        )
    )

    assert completed.response_text == "Workflow completed: 0 planned updates succeeded."
    assert adapter.calls == 0
    async with unit_of_work_factory() as unit_of_work:
        steps = await unit_of_work.workflows.list_steps(frozen.workflow_id)
    assert [step.kind for step in steps] == [
        WorkflowStepKind.DISCOVERY_TOOL,
        WorkflowStepKind.FINAL_RESPONSE,
    ]
