"""Public-safe read and decision facade for workflow-plan approvals."""

from dataclasses import dataclass
from datetime import datetime

from switchboard.application.errors import (
    WorkflowPlanApprovalConflictError,
    WorkflowPlanApprovalNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.application.services.command_idempotency import hash_idempotency_key
from switchboard.application.use_cases.approve_workflow_plan import (
    ApproveWorkflowPlan,
    ApproveWorkflowPlanCommand,
)
from switchboard.application.use_cases.cancel_workflow_plan import (
    CancelWorkflowPlan,
    CancelWorkflowPlanCommand,
    WorkflowPlanCancellationReason,
)
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.command_receipts import ApprovalDecision
from switchboard.domain.identifiers import (
    ActorId,
    TeamId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
)
from switchboard.domain.workflow_approvals import (
    WorkflowPlanActionSummary,
    WorkflowPlanApproval,
)
from switchboard.domain.workflows import WorkflowStatus


@dataclass(frozen=True, slots=True)
class WorkflowPlanApprovalReadModel:
    approval_id: WorkflowPlanApprovalId
    workflow_id: TurnWorkflowId
    requester_actor_id: ActorId
    status: ApprovalStatus
    workflow_status: WorkflowStatus
    safe_actions: tuple[WorkflowPlanActionSummary, ...]
    fingerprint_version: str
    created_at: datetime
    expires_at: datetime
    resolved_by_actor_id: ActorId | None
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class DecideWorkflowPlanApprovalCommand:
    team_id: TeamId
    actor_id: ActorId
    approval_id: WorkflowPlanApprovalId
    decision: ApprovalDecision
    idempotency_key: str


class ManageWorkflowPlanApprovals:
    """Expose safe workflow approval state while execution remains explicit."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        approve: ApproveWorkflowPlan,
        cancel: CancelWorkflowPlan,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._approve = approve
        self._cancel = cancel

    async def get(
        self,
        *,
        team_id: TeamId,
        approval_id: WorkflowPlanApprovalId,
    ) -> WorkflowPlanApprovalReadModel:
        approval, workflow_status = await self._load(team_id, approval_id)
        if approval.status in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        } and approval.is_expired(at=self._clock.now()):
            await self._cancel.execute(
                CancelWorkflowPlanCommand(
                    team_id=team_id,
                    approval_id=approval_id,
                    reason=WorkflowPlanCancellationReason.EXPIRED,
                )
            )
            approval, workflow_status = await self._load(team_id, approval_id)
        return WorkflowPlanApprovalReadModel(
            approval_id=approval.id,
            workflow_id=approval.workflow_id,
            requester_actor_id=approval.requester_actor_id,
            status=approval.status,
            workflow_status=workflow_status,
            safe_actions=approval.safe_actions,
            fingerprint_version=approval.fingerprint.version,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
            resolved_by_actor_id=approval.resolved_by_actor_id,
            resolved_at=approval.resolved_at,
        )

    async def decide(
        self,
        command: DecideWorkflowPlanApprovalCommand,
    ) -> WorkflowPlanApprovalReadModel:
        hash_idempotency_key(command.idempotency_key)
        approval, _ = await self._load(command.team_id, command.approval_id)
        if command.decision is ApprovalDecision.APPROVE:
            if approval.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}:
                raise WorkflowPlanApprovalConflictError(
                    "workflow approval cannot accept an approval decision"
                )
            await self._approve.execute(
                ApproveWorkflowPlanCommand(
                    team_id=command.team_id,
                    actor_id=command.actor_id,
                    approval_id=command.approval_id,
                )
            )
        else:
            if approval.status is ApprovalStatus.REJECTED:
                if approval.resolved_by_actor_id != command.actor_id:
                    raise WorkflowPlanApprovalConflictError(
                        "workflow approval was resolved by another actor"
                    )
                return await self.get(
                    team_id=command.team_id,
                    approval_id=command.approval_id,
                )
            if approval.status is not ApprovalStatus.PENDING:
                raise WorkflowPlanApprovalConflictError(
                    "workflow approval cannot accept a rejection decision"
                )
            await self._cancel.execute(
                CancelWorkflowPlanCommand(
                    team_id=command.team_id,
                    approval_id=command.approval_id,
                    reason=WorkflowPlanCancellationReason.REJECTED,
                    actor_id=command.actor_id,
                )
            )
        return await self.get(
            team_id=command.team_id,
            approval_id=command.approval_id,
        )

    async def _load(
        self,
        team_id: TeamId,
        approval_id: WorkflowPlanApprovalId,
    ) -> tuple[WorkflowPlanApproval, WorkflowStatus]:
        async with self._unit_of_work_factory() as unit_of_work:
            approval = await unit_of_work.workflow_plan_approvals.get(approval_id)
            if approval is None or approval.team_id != team_id:
                raise WorkflowPlanApprovalNotFoundError("workflow approval was not found")
            workflow = await unit_of_work.workflows.get(approval.workflow_id)
            if workflow is None:
                raise WorkflowPlanApprovalConflictError("workflow approval target is invalid")
            return approval, workflow.status
