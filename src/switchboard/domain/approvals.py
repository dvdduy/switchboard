"""Durable policy audit and fingerprint-bound approval lifecycle."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import normalize_utc, require_not_before
from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    ApprovalRequestId,
    PolicyEvaluationId,
    TeamId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.policy import (
    ActionFingerprint,
    PolicyDecision,
    PolicyEnvironment,
    PolicyEvaluation,
    SafeActionSummary,
)
from switchboard.domain.tools import ToolEffect

_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{0,99}$")


class ApprovalStatus(StrEnum):
    """Lifecycle of one durable confirmation request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class PolicyEvaluationRecord:
    """Immutable audit record for one exact policy evaluation."""

    id: PolicyEvaluationId
    team_id: TeamId
    requester_actor_id: ActorId
    agent_version_id: AgentVersionId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    invocation_id: ToolInvocationId | None
    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    effect: ToolEffect
    environment: PolicyEnvironment
    required_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    evaluation: PolicyEvaluation
    fingerprint: ActionFingerprint
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if (
            self.evaluation.decision is PolicyDecision.ALLOW
            and self.effect is not ToolEffect.READ_ONLY
        ):
            raise DomainValidationError("allow decision requires read-only effect")
        if (
            self.evaluation.decision is PolicyDecision.REQUIRE_CONFIRMATION
            and self.effect is not ToolEffect.MUTATING
        ):
            raise DomainValidationError("confirmation decision requires mutating effect")
        object.__setattr__(
            self,
            "required_scopes",
            _validate_scopes(
                self.required_scopes,
                field_name="required_scopes",
                allow_empty=False,
            ),
        )
        object.__setattr__(
            self,
            "granted_scopes",
            _validate_scopes(
                self.granted_scopes,
                field_name="granted_scopes",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "evaluated_at",
            normalize_utc(self.evaluated_at, field_name="evaluated_at"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Durable human decision bound to an immutable policy evaluation."""

    id: ApprovalRequestId
    team_id: TeamId
    policy_evaluation_id: PolicyEvaluationId
    invocation_id: ToolInvocationId
    requester_actor_id: ActorId
    fingerprint: ActionFingerprint
    safe_summary: SafeActionSummary
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    resolved_by_actor_id: ActorId | None = None
    resolved_at: datetime | None = None
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.safe_summary.effect is not ToolEffect.MUTATING:
            raise DomainValidationError("approval request requires mutating effect")
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
                raise DomainValidationError("pending approval must not be resolved")
        elif self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            if self.resolved_by_actor_id is None or resolved_at is None:
                raise DomainValidationError("human decision requires actor and timestamp")
            if resolved_at >= expires_at:
                raise DomainValidationError("human decision must occur before expiry")
            if consumed_at is not None:
                raise DomainValidationError("unconsumed decision must not have consumed_at")
        elif self.status is ApprovalStatus.EXPIRED:
            if self.resolved_by_actor_id is not None or resolved_at is None:
                raise DomainValidationError("expired approval requires only resolved_at")
            if resolved_at < expires_at:
                raise DomainValidationError("approval cannot expire before expires_at")
            if consumed_at is not None:
                raise DomainValidationError("expired approval must not have consumed_at")
        elif self.status is ApprovalStatus.CONSUMED:
            if self.resolved_by_actor_id is None or resolved_at is None or consumed_at is None:
                raise DomainValidationError("consumed approval requires decision and timestamps")
            if resolved_at >= expires_at or consumed_at >= expires_at:
                raise DomainValidationError("approval must be consumed before expiry")

    def is_expired(self, *, at: datetime) -> bool:
        """Return whether the request has reached its exclusive expiry boundary."""

        return normalize_utc(at, field_name="at") >= self.expires_at

    def approve(self, *, actor_id: ActorId, at: datetime) -> "ApprovalRequest":
        return self._human_decision(
            status=ApprovalStatus.APPROVED,
            actor_id=actor_id,
            at=at,
        )

    def reject(self, *, actor_id: ActorId, at: datetime) -> "ApprovalRequest":
        return self._human_decision(
            status=ApprovalStatus.REJECTED,
            actor_id=actor_id,
            at=at,
        )

    def expire(self, *, at: datetime) -> "ApprovalRequest":
        if self.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            raise InvalidStateTransition(f"cannot expire approval from {self.status.value}")
        expired_at = normalize_utc(at, field_name="at")
        if expired_at < self.expires_at:
            raise InvalidStateTransition("approval has not expired")
        return replace(
            self,
            status=ApprovalStatus.EXPIRED,
            resolved_by_actor_id=None,
            resolved_at=expired_at,
            consumed_at=None,
        )

    def consume(self, *, at: datetime) -> "ApprovalRequest":
        if self.status is not ApprovalStatus.APPROVED:
            raise InvalidStateTransition(f"cannot consume approval from {self.status.value}")
        consumed_at = normalize_utc(at, field_name="at")
        if consumed_at >= self.expires_at:
            raise InvalidStateTransition("approval is expired")
        return replace(
            self,
            status=ApprovalStatus.CONSUMED,
            consumed_at=consumed_at,
        )

    def _human_decision(
        self,
        *,
        status: ApprovalStatus,
        actor_id: ActorId,
        at: datetime,
    ) -> "ApprovalRequest":
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidStateTransition(f"cannot decide approval from {self.status.value}")
        resolved_at = normalize_utc(at, field_name="at")
        if resolved_at >= self.expires_at:
            raise InvalidStateTransition("approval is expired")
        return replace(
            self,
            status=status,
            resolved_by_actor_id=actor_id,
            resolved_at=resolved_at,
        )


def _validate_scopes(
    scopes: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    normalized = tuple(sorted(set(scopes)))
    if not allow_empty and not normalized:
        raise DomainValidationError(f"{field_name} must not be empty")
    if len(normalized) > 32 or any(not _SCOPE_PATTERN.fullmatch(scope) for scope in normalized):
        raise DomainValidationError(f"{field_name} is invalid")
    return normalized
