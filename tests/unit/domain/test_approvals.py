from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    PolicyEvaluationRecord,
)
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
    ACTION_FINGERPRINT_VERSION,
    POLICY_VERSION,
    ActionFingerprint,
    PolicyDecision,
    PolicyEnvironment,
    PolicyEvaluation,
    PolicyReasonCode,
    SafeActionSummary,
)
from switchboard.domain.tools import ToolEffect

NOW = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)


def fingerprint() -> ActionFingerprint:
    return ActionFingerprint(version=ACTION_FINGERPRINT_VERSION, digest="a" * 64)


def pending_approval() -> ApprovalRequest:
    return ApprovalRequest(
        id=ApprovalRequestId(uuid4()),
        team_id=TeamId(uuid4()),
        policy_evaluation_id=PolicyEvaluationId(uuid4()),
        invocation_id=ToolInvocationId(uuid4()),
        requester_actor_id=ActorId(uuid4()),
        fingerprint=fingerprint(),
        safe_summary=SafeActionSummary(
            tool_definition_id=ToolDefinitionId(uuid4()),
            tool_version_id=ToolVersionId(uuid4()),
            effect=ToolEffect.MUTATING,
            argument_fields=("due_date", "task_id"),
        ),
        status=ApprovalStatus.PENDING,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def test_policy_evaluation_record_normalizes_audit_fields() -> None:
    record = PolicyEvaluationRecord(
        id=PolicyEvaluationId(uuid4()),
        team_id=TeamId(uuid4()),
        requester_actor_id=ActorId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        invocation_id=None,
        tool_definition_id=ToolDefinitionId(uuid4()),
        tool_version_id=ToolVersionId(uuid4()),
        effect=ToolEffect.MUTATING,
        environment=PolicyEnvironment.DEVELOPMENT,
        required_scopes=("work_items:write",),
        granted_scopes=("projects:read", "work_items:write", "projects:read"),
        evaluation=PolicyEvaluation(
            policy_version=POLICY_VERSION,
            decision=PolicyDecision.REQUIRE_CONFIRMATION,
            reason_code=PolicyReasonCode.MUTATION_CONFIRMATION_REQUIRED,
        ),
        fingerprint=fingerprint(),
        evaluated_at=NOW,
    )

    assert record.granted_scopes == ("projects:read", "work_items:write")


def test_pending_approval_can_be_approved_and_consumed_before_expiry() -> None:
    actor_id = ActorId(uuid4())
    approved = pending_approval().approve(actor_id=actor_id, at=NOW + timedelta(minutes=1))
    consumed = approved.consume(at=NOW + timedelta(minutes=2))

    assert approved.status is ApprovalStatus.APPROVED
    assert approved.resolved_by_actor_id == actor_id
    assert consumed.status is ApprovalStatus.CONSUMED
    assert consumed.consumed_at == NOW + timedelta(minutes=2)


def test_pending_approval_can_be_rejected() -> None:
    rejected = pending_approval().reject(
        actor_id=ActorId(uuid4()),
        at=NOW + timedelta(minutes=1),
    )

    assert rejected.status is ApprovalStatus.REJECTED
    assert rejected.consumed_at is None


@pytest.mark.parametrize("starting_status", [ApprovalStatus.PENDING, ApprovalStatus.APPROVED])
def test_pending_or_approved_request_can_expire_lazily(
    starting_status: ApprovalStatus,
) -> None:
    approval = pending_approval()
    if starting_status is ApprovalStatus.APPROVED:
        approval = approval.approve(actor_id=ActorId(uuid4()), at=NOW + timedelta(minutes=1))

    expired = approval.expire(at=approval.expires_at)

    assert expired.status is ApprovalStatus.EXPIRED
    assert expired.resolved_by_actor_id is None
    assert expired.resolved_at == approval.expires_at


def test_expiry_boundary_blocks_human_decision_and_consumption() -> None:
    pending = pending_approval()
    approved = pending.approve(actor_id=ActorId(uuid4()), at=NOW + timedelta(minutes=1))

    with pytest.raises(InvalidStateTransition, match="expired"):
        pending.approve(actor_id=ActorId(uuid4()), at=pending.expires_at)
    with pytest.raises(InvalidStateTransition, match="expired"):
        approved.consume(at=approved.expires_at)


@pytest.mark.parametrize(
    "transition",
    [
        lambda approval: approval.approve(actor_id=ActorId(uuid4()), at=NOW),
        lambda approval: approval.reject(actor_id=ActorId(uuid4()), at=NOW),
        lambda approval: approval.consume(at=NOW),
        lambda approval: approval.expire(at=NOW + timedelta(minutes=11)),
    ],
)
def test_terminal_approval_cannot_transition_again(
    transition: Callable[[ApprovalRequest], ApprovalRequest],
) -> None:
    rejected = pending_approval().reject(actor_id=ActorId(uuid4()), at=NOW)

    with pytest.raises(InvalidStateTransition):
        transition(rejected)


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        (
            lambda: replace(pending_approval(), expires_at=NOW),
            "expires_at must be after",
        ),
        (
            lambda: replace(
                pending_approval(),
                status=ApprovalStatus.APPROVED,
                resolved_at=NOW,
            ),
            "actor and timestamp",
        ),
        (
            lambda: replace(
                pending_approval(),
                status=ApprovalStatus.EXPIRED,
                resolved_at=NOW,
            ),
            "cannot expire before",
        ),
        (
            lambda: replace(
                pending_approval(),
                safe_summary=replace(
                    pending_approval().safe_summary,
                    effect=ToolEffect.READ_ONLY,
                ),
            ),
            "requires mutating effect",
        ),
    ],
)
def test_approval_rejects_inconsistent_lifecycle_fields(
    invalid: Callable[[], ApprovalRequest],
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        invalid()
