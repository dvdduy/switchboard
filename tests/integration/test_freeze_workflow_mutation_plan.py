import asyncio
from uuid import uuid4

import pytest

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import update_due_date_manifest
from switchboard.application.errors import (
    WorkflowPlanningConflictError,
    WorkflowPlanValidationError,
)
from switchboard.application.ports.tool_adapter import ToolInvocationSuccess
from switchboard.application.use_cases.freeze_workflow_mutation_plan import (
    MAX_WORKFLOW_MUTATIONS,
    FreezeWorkflowMutationPlan,
    FreezeWorkflowMutationPlanCommand,
)
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ExecutionEventId,
    PolicyEvaluationId,
    ToolInvocationId,
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import (
    WORKFLOW_PLAN_FINGERPRINT_VERSION,
    WorkflowStatus,
    WorkflowStepKind,
)
from tests.integration.test_run_turn import activate_tool
from tests.integration.test_run_workflow_discovery import (
    ACTOR_ID,
    FixedClock,
    Generator,
    InspectingDiscoveryAdapter,
    discovery_service,
    setup_command,
)


def planner(factory: SqlAlchemyUnitOfWorkFactory) -> FreezeWorkflowMutationPlan:
    return FreezeWorkflowMutationPlan(
        unit_of_work_factory=factory,
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        invocation_ids=Generator(lambda: ToolInvocationId(uuid4())),
        policy_evaluation_ids=Generator(lambda: PolicyEvaluationId(uuid4())),
        approval_ids=Generator(lambda: WorkflowPlanApprovalId(uuid4())),
        step_ids=Generator(lambda: WorkflowStepId(uuid4())),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    )


def test_planner_rejects_more_than_the_mutation_bound() -> None:
    result = {"items": tuple({"id": f"WI-{index}"} for index in range(MAX_WORKFLOW_MUTATIONS + 1))}

    with pytest.raises(WorkflowPlanValidationError, match="exceeds mutation bound"):
        FreezeWorkflowMutationPlan._extract_targets(result)


async def prepared_plan_command(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    items: list[dict[str, object]],
) -> FreezeWorkflowMutationPlanCommand:
    discovery_command = await setup_command(factory)
    output = {
        "items": [
            {
                "id": item["id"],
                "title": f"Item {index}",
                "status": "open",
                "due_date": "2026-07-10",
            }
            for index, item in enumerate(items)
        ]
    }
    adapter = InspectingDiscoveryAdapter(
        factory,
        discovery_command.turn_id,
        result=ToolInvocationSuccess(output),
    )
    await discovery_service(factory, adapter).execute(discovery_command)
    mutation_version = await activate_tool(
        factory,
        turn_agent_version_id=discovery_command.agent_version_id,
        team_id=discovery_command.team_id,
        candidate=update_due_date_manifest(),
    )
    return FreezeWorkflowMutationPlanCommand(
        team_id=discovery_command.team_id,
        actor_id=ACTOR_ID,
        agent_version_id=discovery_command.agent_version_id,
        turn_id=discovery_command.turn_id,
        attempt_id=discovery_command.attempt_id,
        mutation_tool_version_id=mutation_version.id,
        target_due_date="2026-07-17",
        granted_scopes=("work_items:read", "work_items:write"),
    )


async def test_plan_freeze_persists_exact_linear_mutations_and_one_safe_pause(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}, {"id": "WI-2"}],
    )

    result = await planner(unit_of_work_factory).execute(command)

    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(result.workflow_id)
        steps = await unit_of_work.workflows.list_steps(result.workflow_id)
        approval = await unit_of_work.workflow_plan_approvals.get_for_workflow(result.workflow_id)
        invocations = await unit_of_work.tool_invocations.list_for_turn(command.turn_id)
        turn = await unit_of_work.turns.get(command.turn_id)
        attempt = await unit_of_work.turns.get_attempt(command.attempt_id)
        events = await unit_of_work.turns.list_events(
            turn_id=command.turn_id,
            after_sequence=0,
            limit=20,
        )

    assert result.awaiting_confirmation is True
    assert result.mutation_count == 2
    assert workflow is not None and workflow.status is WorkflowStatus.AWAITING_CONFIRMATION
    assert workflow.plan_fingerprint is not None
    assert workflow.plan_fingerprint.version == WORKFLOW_PLAN_FINGERPRINT_VERSION
    assert approval is not None and approval.id == result.approval_id
    assert approval.fingerprint == workflow.plan_fingerprint
    assert [step.kind for step in steps] == [
        WorkflowStepKind.DISCOVERY_TOOL,
        WorkflowStepKind.MUTATION_TOOL,
        WorkflowStepKind.MUTATION_TOOL,
        WorkflowStepKind.FINAL_RESPONSE,
    ]
    assert [step.predecessor_step_id for step in steps[1:]] == [step.id for step in steps[:-1]]
    assert [invocation.invocation_number for invocation in invocations] == [1, 2, 3]
    assert [invocation.status for invocation in invocations[1:]] == [
        ToolInvocationStatus.AWAITING_CONFIRMATION,
        ToolInvocationStatus.AWAITING_CONFIRMATION,
    ]
    assert [invocation.arguments["work_item_id"] for invocation in invocations[1:]] == [
        "WI-1",
        "WI-2",
    ]
    assert all(
        invocation.idempotency_key == f"invocation:{invocation.id}"
        for invocation in invocations[1:]
    )
    assert turn is not None and turn.status is TurnStatus.AWAITING_CONFIRMATION
    assert attempt is not None and attempt.status is TurnAttemptStatus.AWAITING_CONFIRMATION
    pause = events[-1]
    assert pause.kind is ExecutionEventKind.APPROVAL_REQUIRED
    assert pause.payload["mutation_count"] == 2
    assert "2026-07-17" not in repr(pause.payload)
    assert "WI-1" not in repr(pause.payload)
    assert workflow.plan_fingerprint.digest not in repr(pause.payload)


async def test_duplicate_targets_abort_without_partial_plan(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}, {"id": "WI-1"}],
    )

    with pytest.raises(WorkflowPlanValidationError, match="duplicate"):
        await planner(unit_of_work_factory).execute(command)

    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get_for_turn(command.turn_id)
        invocations = await unit_of_work.tool_invocations.list_for_turn(command.turn_id)
        approval = (
            None
            if workflow is None
            else await unit_of_work.workflow_plan_approvals.get_for_workflow(workflow.id)
        )
    assert workflow is not None and workflow.status is WorkflowStatus.PLANNING
    assert len(invocations) == 1
    assert approval is None


async def test_zero_target_plan_freezes_without_approval(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await prepared_plan_command(unit_of_work_factory, items=[])

    result = await planner(unit_of_work_factory).execute(command)

    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(result.workflow_id)
        steps = await unit_of_work.workflows.list_steps(result.workflow_id)
        approval = await unit_of_work.workflow_plan_approvals.get_for_workflow(result.workflow_id)
    assert result.approval_id is None
    assert result.awaiting_confirmation is False
    assert workflow is not None and workflow.status is WorkflowStatus.COMPLETING
    assert [step.kind for step in steps] == [
        WorkflowStepKind.DISCOVERY_TOOL,
        WorkflowStepKind.FINAL_RESPONSE,
    ]
    assert approval is None


async def test_concurrent_plan_freeze_has_one_winner(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}],
    )

    results = await asyncio.gather(
        planner(unit_of_work_factory).execute(command),
        planner(unit_of_work_factory).execute(command),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, WorkflowPlanningConflictError) for result in results) == 1
    async with unit_of_work_factory() as unit_of_work:
        invocations = await unit_of_work.tool_invocations.list_for_turn(command.turn_id)
    assert len(invocations) == 2
