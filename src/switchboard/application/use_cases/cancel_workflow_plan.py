"""Reject or expire a frozen workflow before any mutation dispatch."""

from dataclasses import dataclass
from enum import StrEnum

from switchboard.application.errors import (
    WorkflowPlanApprovalConflictError,
    WorkflowPlanApprovalNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    ExecutionEventId,
    TeamId,
    WorkflowPlanApprovalId,
)
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import (
    WorkflowStatus,
    WorkflowStepKind,
    WorkflowStepStatus,
)


class WorkflowPlanCancellationReason(StrEnum):
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class CancelWorkflowPlanCommand:
    team_id: TeamId
    approval_id: WorkflowPlanApprovalId
    reason: WorkflowPlanCancellationReason
    actor_id: ActorId | None = None

    def __post_init__(self) -> None:
        if (self.reason is WorkflowPlanCancellationReason.REJECTED) != (self.actor_id is not None):
            raise DomainValidationError("rejection requires an actor and expiry forbids one")


class CancelWorkflowPlan:
    """Atomically resolve approval and cancel every never-dispatched plan record."""

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

    async def execute(self, command: CancelWorkflowPlanCommand) -> ApprovalStatus:
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
            if (
                workflow is None
                or workflow.id != approval.workflow_id
                or workflow.status is not WorkflowStatus.AWAITING_CONFIRMATION
                or workflow.approval_id != approval.id
                or turn is None
                or turn.status is not TurnStatus.AWAITING_CONFIRMATION
            ):
                raise WorkflowPlanApprovalConflictError("workflow is not cancellable")
            attempt = await unit_of_work.turns.get_attempt(workflow.attempt_id)
            if attempt is None or attempt.status is not TurnAttemptStatus.AWAITING_CONFIRMATION:
                raise WorkflowPlanApprovalConflictError("workflow attempt is not cancellable")
            at = self._clock.now()
            if command.reason is WorkflowPlanCancellationReason.EXPIRED:
                if not approval.is_expired(at=at):
                    raise WorkflowPlanApprovalConflictError("workflow approval has not expired")
                resolved = approval.expire(at=at)
                failure_code = "approval_expired"
            else:
                if approval.status is not ApprovalStatus.PENDING or command.actor_id is None:
                    raise WorkflowPlanApprovalConflictError("workflow approval cannot be rejected")
                resolved = approval.reject(actor_id=command.actor_id, at=at)
                failure_code = "approval_rejected"

            steps = await unit_of_work.workflows.list_steps(workflow.id)
            for step in steps:
                if step.kind is WorkflowStepKind.DISCOVERY_TOOL:
                    continue
                if step.status is not WorkflowStepStatus.PENDING:
                    raise WorkflowPlanApprovalConflictError("workflow contains a dispatched step")
                if step.invocation_id is not None:
                    invocation = await unit_of_work.tool_invocations.get(step.invocation_id)
                    if (
                        invocation is None
                        or invocation.status is not ToolInvocationStatus.AWAITING_CONFIRMATION
                    ):
                        raise WorkflowPlanApprovalConflictError(
                            "workflow contains a dispatched invocation"
                        )
                    await unit_of_work.tool_invocations.update_lifecycle(
                        previous=invocation,
                        updated=invocation.cancel(at=at),
                    )
                await unit_of_work.workflows.update_step_lifecycle(
                    previous=step,
                    updated=step.skip(at=at, failure_code=failure_code),
                )

            await unit_of_work.workflow_plan_approvals.update_lifecycle(
                previous=approval,
                updated=resolved,
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.APPROVAL_RESOLVED,
                payload={
                    "approval_id": str(approval.id),
                    "decision": command.reason.value,
                    "workflow_id": str(workflow.id),
                },
                occurred_at=at,
            )
            cancelled_workflow = workflow.cancel(at=at)
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.WORKFLOW_TERMINAL,
                payload={
                    "mutation_count": sum(
                        step.kind is WorkflowStepKind.MUTATION_TOOL for step in steps
                    ),
                    "status": cancelled_workflow.status.value,
                    "workflow_id": str(workflow.id),
                },
                occurred_at=at,
            )
            await unit_of_work.workflows.update_lifecycle(
                previous=workflow,
                updated=cancelled_workflow,
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.cancel(at=at),
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.cancel(at=at),
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_CANCELLED,
                payload={"reason": failure_code, "workflow_id": str(workflow.id)},
                occurred_at=at,
            )
            await unit_of_work.commit()
            return resolved.status
