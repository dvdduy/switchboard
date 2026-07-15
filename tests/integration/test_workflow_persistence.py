from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.application.errors import (
    WorkflowLifecycleConflictError,
    WorkflowStepLifecycleConflictError,
)
from switchboard.domain.identifiers import ToolInvocationId, TurnWorkflowId, WorkflowStepId
from switchboard.domain.workflows import (
    WORKFLOW_PLAN_FINGERPRINT_VERSION,
    TurnWorkflow,
    WorkflowPlanFingerprint,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepKind,
    WorkflowStepStatus,
)
from tests.integration.support import seed_turn
from tests.integration.test_tool_invocation_persistence import pending_invocation

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def workflow_for(*, turn_id, attempt_id) -> TurnWorkflow:
    return TurnWorkflow(
        id=TurnWorkflowId(uuid4()),
        turn_id=turn_id,
        attempt_id=attempt_id,
        status=WorkflowStatus.DISCOVERY_PENDING,
        plan_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def discovery_step(*, workflow: TurnWorkflow, invocation_id) -> WorkflowStep:
    return WorkflowStep(
        id=WorkflowStepId(uuid4()),
        workflow_id=workflow.id,
        turn_id=workflow.turn_id,
        attempt_id=workflow.attempt_id,
        step_number=1,
        kind=WorkflowStepKind.DISCOVERY_TOOL,
        status=WorkflowStepStatus.PENDING,
        invocation_id=invocation_id,
        created_at=NOW,
    )


async def test_workflow_round_trip_and_focused_lifecycle_update(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    pending = workflow_for(turn_id=turn.id, attempt_id=attempt.id)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.add(pending)
        await unit_of_work.commit()

    running = pending.start_discovery(at=NOW + timedelta(seconds=1))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.update_lifecycle(previous=pending, updated=running)
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        stored = await unit_of_work.workflows.get(pending.id)
        for_turn = await unit_of_work.workflows.get_for_turn(turn.id)

    assert stored == running
    assert for_turn == running


async def test_stale_workflow_lifecycle_update_is_rejected(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    pending = workflow_for(turn_id=turn.id, attempt_id=attempt.id)
    running = pending.start_discovery(at=NOW + timedelta(seconds=1))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.add(pending)
        await unit_of_work.commit()
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.update_lifecycle(previous=pending, updated=running)
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(WorkflowLifecycleConflictError):
            await unit_of_work.workflows.update_lifecycle(previous=pending, updated=running)


async def test_database_enforces_one_workflow_per_turn_and_attempt_ownership(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    _, other_attempt = await seed_turn(unit_of_work_factory, now=NOW)
    first = workflow_for(turn_id=turn.id, attempt_id=attempt.id)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.add(first)
        with pytest.raises(IntegrityError):
            await unit_of_work.workflows.add(replace(first, id=TurnWorkflowId(uuid4())))

    mismatched = workflow_for(turn_id=turn.id, attempt_id=other_attempt.id)
    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.workflows.add(mismatched)


async def test_step_round_trip_order_and_focused_lifecycle_update(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    invocation = await pending_invocation(unit_of_work_factory)
    workflow = workflow_for(turn_id=invocation.turn_id, attempt_id=invocation.attempt_id)
    pending = discovery_step(workflow=workflow, invocation_id=invocation.id)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(invocation)
        await unit_of_work.workflows.add(workflow)
        await unit_of_work.workflows.add_step(pending)
        await unit_of_work.commit()

    running = pending.start(at=NOW + timedelta(seconds=1))
    succeeded = running.succeed(at=NOW + timedelta(seconds=2))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.update_step_lifecycle(
            previous=pending,
            updated=running,
        )
        await unit_of_work.commit()
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.workflows.update_step_lifecycle(
            previous=running,
            updated=succeeded,
        )
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        stored = await unit_of_work.workflows.get_step(pending.id)
        steps = await unit_of_work.workflows.list_steps(workflow.id)

    assert stored == succeeded
    assert steps == (succeeded,)

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(WorkflowStepLifecycleConflictError):
            await unit_of_work.workflows.update_step_lifecycle(
                previous=pending,
                updated=running,
            )


async def test_database_rejects_step_invocation_from_another_attempt(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    first = await pending_invocation(unit_of_work_factory)
    second = await pending_invocation(unit_of_work_factory)
    workflow = workflow_for(turn_id=first.turn_id, attempt_id=first.attempt_id)
    mismatched = discovery_step(workflow=workflow, invocation_id=second.id)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(first)
        await unit_of_work.tool_invocations.add(second)
        await unit_of_work.workflows.add(workflow)
        with pytest.raises(IntegrityError):
            await unit_of_work.workflows.add_step(mismatched)


async def test_database_enforces_unique_step_order(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    first_invocation = await pending_invocation(unit_of_work_factory)
    second_invocation = replace(
        first_invocation,
        id=ToolInvocationId(uuid4()),
        invocation_number=2,
        idempotency_key=f"invocation:{uuid4()}",
    )
    third_invocation = replace(
        first_invocation,
        id=ToolInvocationId(uuid4()),
        invocation_number=3,
        idempotency_key=f"invocation:{uuid4()}",
    )
    workflow = workflow_for(
        turn_id=first_invocation.turn_id,
        attempt_id=first_invocation.attempt_id,
    )
    first = discovery_step(workflow=workflow, invocation_id=first_invocation.id)
    running = workflow.start_discovery(at=NOW)
    planning = running.begin_planning(at=NOW)
    second = WorkflowStep(
        id=WorkflowStepId(uuid4()),
        workflow_id=workflow.id,
        turn_id=workflow.turn_id,
        attempt_id=workflow.attempt_id,
        step_number=2,
        kind=WorkflowStepKind.MUTATION_TOOL,
        status=WorkflowStepStatus.PENDING,
        predecessor_step_id=first.id,
        predecessor_step_number=1,
        invocation_id=second_invocation.id,
        created_at=NOW,
    )
    duplicate_order = replace(
        second,
        id=WorkflowStepId(uuid4()),
        invocation_id=third_invocation.id,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(first_invocation)
        await unit_of_work.tool_invocations.add(second_invocation)
        await unit_of_work.tool_invocations.add(third_invocation)
        await unit_of_work.workflows.add(workflow)
        await unit_of_work.workflows.add_step(first)
        await unit_of_work.workflows.update_lifecycle(previous=workflow, updated=running)
        await unit_of_work.workflows.update_lifecycle(previous=running, updated=planning)
        await unit_of_work.workflows.add_step(second)
        with pytest.raises(IntegrityError):
            await unit_of_work.workflows.add_step(duplicate_order)


async def test_database_rejects_predecessor_owned_by_another_workflow(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    first_invocation = await pending_invocation(unit_of_work_factory)
    second_invocation = await pending_invocation(unit_of_work_factory)
    mutation_invocation = replace(
        second_invocation,
        id=ToolInvocationId(uuid4()),
        invocation_number=2,
        idempotency_key=f"invocation:{uuid4()}",
    )
    first_workflow = workflow_for(
        turn_id=first_invocation.turn_id,
        attempt_id=first_invocation.attempt_id,
    )
    second_workflow = workflow_for(
        turn_id=second_invocation.turn_id,
        attempt_id=second_invocation.attempt_id,
    )
    first_discovery = discovery_step(
        workflow=first_workflow,
        invocation_id=first_invocation.id,
    )
    second_discovery = discovery_step(
        workflow=second_workflow,
        invocation_id=second_invocation.id,
    )
    second_running = second_workflow.start_discovery(at=NOW)
    second_planning = second_running.begin_planning(at=NOW)
    mismatched = WorkflowStep(
        id=WorkflowStepId(uuid4()),
        workflow_id=second_workflow.id,
        turn_id=second_workflow.turn_id,
        attempt_id=second_workflow.attempt_id,
        step_number=2,
        kind=WorkflowStepKind.MUTATION_TOOL,
        status=WorkflowStepStatus.PENDING,
        predecessor_step_id=first_discovery.id,
        predecessor_step_number=1,
        invocation_id=mutation_invocation.id,
        created_at=NOW,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(first_invocation)
        await unit_of_work.tool_invocations.add(second_invocation)
        await unit_of_work.tool_invocations.add(mutation_invocation)
        await unit_of_work.workflows.add(first_workflow)
        await unit_of_work.workflows.add(second_workflow)
        await unit_of_work.workflows.add_step(first_discovery)
        await unit_of_work.workflows.add_step(second_discovery)
        await unit_of_work.workflows.update_lifecycle(
            previous=second_workflow,
            updated=second_running,
        )
        await unit_of_work.workflows.update_lifecycle(
            previous=second_running,
            updated=second_planning,
        )
        with pytest.raises(IntegrityError):
            await unit_of_work.workflows.add_step(mismatched)


async def test_repository_rejects_step_insertion_after_plan_freeze(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    invocation = await pending_invocation(unit_of_work_factory)
    pending = workflow_for(turn_id=invocation.turn_id, attempt_id=invocation.attempt_id)
    discovery_running = pending.start_discovery(at=NOW)
    planning = discovery_running.begin_planning(at=NOW)
    frozen = planning.begin_completion_without_mutations(
        fingerprint=WorkflowPlanFingerprint(
            version=WORKFLOW_PLAN_FINGERPRINT_VERSION,
            digest="b" * 64,
        ),
        at=NOW,
    )
    first = discovery_step(workflow=pending, invocation_id=invocation.id)
    final = WorkflowStep(
        id=WorkflowStepId(uuid4()),
        workflow_id=pending.id,
        turn_id=pending.turn_id,
        attempt_id=pending.attempt_id,
        step_number=2,
        kind=WorkflowStepKind.FINAL_RESPONSE,
        status=WorkflowStepStatus.PENDING,
        predecessor_step_id=first.id,
        predecessor_step_number=1,
        created_at=NOW,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(invocation)
        await unit_of_work.workflows.add(pending)
        await unit_of_work.workflows.add_step(first)
        await unit_of_work.workflows.update_lifecycle(
            previous=pending,
            updated=discovery_running,
        )
        await unit_of_work.workflows.update_lifecycle(
            previous=discovery_running,
            updated=planning,
        )
        await unit_of_work.workflows.update_lifecycle(previous=planning, updated=frozen)
        with pytest.raises(ValueError, match="not extensible"):
            await unit_of_work.workflows.add_step(final)
