"""Resolve one internal workflow-plan approval without dispatching mutations."""

from dataclasses import dataclass

from switchboard.application.errors import (
    WorkflowPlanApprovalConflictError,
    WorkflowPlanApprovalNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    ExecutionEventId,
    TeamId,
    WorkflowPlanApprovalId,
)
from switchboard.domain.turns import TurnStatus
from switchboard.domain.workflows import WorkflowStatus


@dataclass(frozen=True, slots=True)
class ApproveWorkflowPlanCommand:
    team_id: TeamId
    actor_id: ActorId
    approval_id: WorkflowPlanApprovalId


class ApproveWorkflowPlan:
    """Record the human decision while leaving execution to an explicit runner."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        event_ids: IdGenerator[ExecutionEventId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._event_ids = event_ids

    async def execute(self, command: ApproveWorkflowPlanCommand) -> ApprovalStatus:
        async with self._unit_of_work_factory() as unit_of_work:
            initial_approval = await unit_of_work.workflow_plan_approvals.get(command.approval_id)
            if initial_approval is None or initial_approval.team_id != command.team_id:
                raise WorkflowPlanApprovalNotFoundError("workflow approval was not found")
            initial_workflow = await unit_of_work.workflows.get(initial_approval.workflow_id)
            if initial_workflow is None:
                raise WorkflowPlanApprovalConflictError("workflow approval target is invalid")
            turn = await unit_of_work.turns.get_for_update(initial_workflow.turn_id)
            workflow = await unit_of_work.workflows.get_for_turn_for_update(
                initial_workflow.turn_id
            )
            approval = await unit_of_work.workflow_plan_approvals.get_for_update(
                command.approval_id
            )
            if approval is None or approval.team_id != command.team_id:
                raise WorkflowPlanApprovalNotFoundError("workflow approval was not found")
            if approval.status in {ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED}:
                if approval.resolved_by_actor_id != command.actor_id:
                    raise WorkflowPlanApprovalConflictError(
                        "workflow approval was resolved by another actor"
                    )
                return approval.status
            if approval.status is not ApprovalStatus.PENDING:
                raise WorkflowPlanApprovalConflictError(
                    f"workflow approval cannot be approved from {approval.status.value}"
                )
            at = self._clock.now()
            if approval.is_expired(at=at):
                raise WorkflowPlanApprovalConflictError("workflow approval is expired")
            if (
                workflow is None
                or workflow.id != approval.workflow_id
                or workflow.status is not WorkflowStatus.AWAITING_CONFIRMATION
                or workflow.approval_id != approval.id
            ):
                raise WorkflowPlanApprovalConflictError("workflow approval target is invalid")
            if turn is None or turn.status is not TurnStatus.AWAITING_CONFIRMATION:
                raise WorkflowPlanApprovalConflictError("workflow is not awaiting confirmation")
            approved = approval.approve(actor_id=command.actor_id, at=at)
            await unit_of_work.workflow_plan_approvals.update_lifecycle(
                previous=approval,
                updated=approved,
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=workflow.attempt_id,
                kind=ExecutionEventKind.APPROVAL_RESOLVED,
                payload={
                    "approval_id": str(approval.id),
                    "decision": "approved",
                    "workflow_id": str(workflow.id),
                },
                occurred_at=at,
            )
            await unit_of_work.commit()
            return approved.status
