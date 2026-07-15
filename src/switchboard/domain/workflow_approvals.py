"""Durable approval over one exact ordered workflow mutation plan."""

from dataclasses import dataclass, replace
from datetime import datetime

from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.common import normalize_utc, require_not_before, require_positive
from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    ActorId,
    TeamId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
)
from switchboard.domain.policy import SafeActionSummary
from switchboard.domain.tools import ToolEffect
from switchboard.domain.workflows import WorkflowPlanFingerprint


@dataclass(frozen=True, slots=True)
class WorkflowPlanActionSummary:
    """Ordered, value-free public shape of one planned mutation."""

    step_number: int
    action: SafeActionSummary

    def __post_init__(self) -> None:
        require_positive(self.step_number, field_name="step_number")
        if self.action.effect is not ToolEffect.MUTATING:
            raise DomainValidationError("workflow approval actions must be mutating")


@dataclass(frozen=True, slots=True)
class WorkflowPlanApproval:
    """One human decision bound to a frozen workflow-plan fingerprint."""

    id: WorkflowPlanApprovalId
    workflow_id: TurnWorkflowId
    team_id: TeamId
    requester_actor_id: ActorId
    fingerprint: WorkflowPlanFingerprint
    safe_actions: tuple[WorkflowPlanActionSummary, ...]
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    resolved_by_actor_id: ActorId | None = None
    resolved_at: datetime | None = None
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.safe_actions:
            raise DomainValidationError("workflow plan approval requires at least one action")
        numbers = tuple(summary.step_number for summary in self.safe_actions)
        if numbers != tuple(sorted(set(numbers))):
            raise DomainValidationError(
                "workflow approval actions must have unique increasing steps"
            )

        created_at = normalize_utc(self.created_at, field_name="created_at")
        expires_at = normalize_utc(self.expires_at, field_name="expires_at")
        if expires_at <= created_at:
            raise DomainValidationError("expires_at must be after created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)

        resolved_at = self.resolved_at
        if resolved_at is not None:
            resolved_at = normalize_utc(resolved_at, field_name="resolved_at")
            require_not_before(
                resolved_at,
                minimum=created_at,
                field_name="resolved_at",
                minimum_field_name="created_at",
            )
            object.__setattr__(self, "resolved_at", resolved_at)
        consumed_at = self.consumed_at
        if consumed_at is not None:
            consumed_at = normalize_utc(consumed_at, field_name="consumed_at")
            if resolved_at is None:
                raise DomainValidationError("consumed approval requires resolved_at")
            require_not_before(
                consumed_at,
                minimum=resolved_at,
                field_name="consumed_at",
                minimum_field_name="resolved_at",
            )
            object.__setattr__(self, "consumed_at", consumed_at)

        if self.status is ApprovalStatus.PENDING:
            if any(
                value is not None for value in (self.resolved_by_actor_id, resolved_at, consumed_at)
            ):
                raise DomainValidationError("pending workflow approval must not be resolved")
        elif self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            if self.resolved_by_actor_id is None or resolved_at is None:
                raise DomainValidationError("workflow approval decision requires actor and time")
            if resolved_at >= expires_at or consumed_at is not None:
                raise DomainValidationError("workflow approval decision fields are invalid")
        elif self.status is ApprovalStatus.EXPIRED:
            if self.resolved_by_actor_id is not None or resolved_at is None:
                raise DomainValidationError("expired workflow approval requires only resolved_at")
            if resolved_at < expires_at or consumed_at is not None:
                raise DomainValidationError("workflow approval expiry fields are invalid")
        elif self.status is ApprovalStatus.CONSUMED:
            if self.resolved_by_actor_id is None or resolved_at is None or consumed_at is None:
                raise DomainValidationError(
                    "consumed workflow approval requires decision and times"
                )
            if resolved_at >= expires_at or consumed_at >= expires_at:
                raise DomainValidationError("workflow approval must be consumed before expiry")

    def is_expired(self, *, at: datetime) -> bool:
        return normalize_utc(at, field_name="at") >= self.expires_at

    def approve(self, *, actor_id: ActorId, at: datetime) -> "WorkflowPlanApproval":
        return self._human_decision(status=ApprovalStatus.APPROVED, actor_id=actor_id, at=at)

    def reject(self, *, actor_id: ActorId, at: datetime) -> "WorkflowPlanApproval":
        return self._human_decision(status=ApprovalStatus.REJECTED, actor_id=actor_id, at=at)

    def expire(self, *, at: datetime) -> "WorkflowPlanApproval":
        if self.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            raise InvalidStateTransition(
                f"cannot expire workflow approval from {self.status.value}"
            )
        expired_at = normalize_utc(at, field_name="at")
        if expired_at < self.expires_at:
            raise InvalidStateTransition("workflow approval has not expired")
        return replace(
            self,
            status=ApprovalStatus.EXPIRED,
            resolved_by_actor_id=None,
            resolved_at=expired_at,
            consumed_at=None,
        )

    def consume(self, *, at: datetime) -> "WorkflowPlanApproval":
        if self.status is not ApprovalStatus.APPROVED:
            raise InvalidStateTransition(
                f"cannot consume workflow approval from {self.status.value}"
            )
        consumed_at = normalize_utc(at, field_name="at")
        if consumed_at >= self.expires_at:
            raise InvalidStateTransition("workflow approval is expired")
        return replace(self, status=ApprovalStatus.CONSUMED, consumed_at=consumed_at)

    def _human_decision(
        self,
        *,
        status: ApprovalStatus,
        actor_id: ActorId,
        at: datetime,
    ) -> "WorkflowPlanApproval":
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidStateTransition(
                f"cannot decide workflow approval from {self.status.value}"
            )
        resolved_at = normalize_utc(at, field_name="at")
        if resolved_at >= self.expires_at:
            raise InvalidStateTransition("workflow approval is expired")
        return replace(
            self,
            status=status,
            resolved_by_actor_id=actor_id,
            resolved_at=resolved_at,
        )
