from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    MessageId,
    ToolInvocationId,
    TurnAttemptId,
    TurnId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.workflows import (
    WORKFLOW_PLAN_FINGERPRINT_VERSION,
    TurnWorkflow,
    WorkflowPlanFingerprint,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepKind,
    WorkflowStepStatus,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def fingerprint() -> WorkflowPlanFingerprint:
    return WorkflowPlanFingerprint(
        version=WORKFLOW_PLAN_FINGERPRINT_VERSION,
        digest="a" * 64,
    )


def pending_workflow() -> TurnWorkflow:
    return TurnWorkflow(
        id=TurnWorkflowId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        status=WorkflowStatus.DISCOVERY_PENDING,
        plan_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def pending_tool_step(
    *,
    kind: WorkflowStepKind = WorkflowStepKind.DISCOVERY_TOOL,
    step_number: int = 1,
    predecessor: WorkflowStep | None = None,
) -> WorkflowStep:
    return WorkflowStep(
        id=WorkflowStepId(uuid4()),
        workflow_id=(TurnWorkflowId(uuid4()) if predecessor is None else predecessor.workflow_id),
        turn_id=TurnId(uuid4()) if predecessor is None else predecessor.turn_id,
        attempt_id=(TurnAttemptId(uuid4()) if predecessor is None else predecessor.attempt_id),
        step_number=step_number,
        kind=kind,
        status=WorkflowStepStatus.PENDING,
        predecessor_step_id=None if predecessor is None else predecessor.id,
        predecessor_step_number=(None if predecessor is None else predecessor.step_number),
        invocation_id=ToolInvocationId(uuid4()),
        created_at=NOW,
    )


def test_workflow_lifecycle_freezes_plan_and_finishes_with_output() -> None:
    approval_id = WorkflowPlanApprovalId(uuid4())
    output_id = MessageId(uuid4())
    pending = pending_workflow()

    discovery = pending.start_discovery(at=NOW + timedelta(seconds=1))
    planning = discovery.begin_planning(at=NOW + timedelta(seconds=2))
    awaiting = planning.await_confirmation(
        fingerprint=fingerprint(),
        approval_id=approval_id,
        at=NOW + timedelta(seconds=3),
    )
    running = awaiting.resume(at=NOW + timedelta(seconds=4))
    completing = running.begin_completion(at=NOW + timedelta(seconds=5))
    completed = completing.complete(
        output_message_id=output_id,
        at=NOW + timedelta(seconds=6),
    )

    assert awaiting.plan_fingerprint == fingerprint()
    assert awaiting.approval_id == approval_id
    assert completed.status is WorkflowStatus.COMPLETED
    assert completed.output_message_id == output_id
    assert completed.completed_at == NOW + timedelta(seconds=6)


def test_zero_mutation_plan_can_complete_without_approval() -> None:
    planning = pending_workflow().start_discovery(at=NOW).begin_planning(at=NOW)
    completing = planning.begin_completion_without_mutations(
        fingerprint=fingerprint(),
        at=NOW,
    )

    assert completing.status is WorkflowStatus.COMPLETING
    assert completing.approval_id is None


def test_discovery_can_fail_before_plan_freeze() -> None:
    running = pending_workflow().start_discovery(at=NOW)

    failed = running.fail_discovery(at=NOW + timedelta(seconds=1))

    assert failed.status is WorkflowStatus.DISCOVERY_FAILED
    assert failed.plan_fingerprint is None
    assert failed.completed_at == NOW + timedelta(seconds=1)


def test_awaiting_workflow_can_cancel_without_output() -> None:
    awaiting = (
        pending_workflow()
        .start_discovery(at=NOW)
        .begin_planning(at=NOW)
        .await_confirmation(
            fingerprint=fingerprint(),
            approval_id=WorkflowPlanApprovalId(uuid4()),
            at=NOW,
        )
    )

    cancelled = awaiting.cancel(at=NOW + timedelta(seconds=1))

    assert cancelled.status is WorkflowStatus.CANCELLED
    assert cancelled.output_message_id is None


def test_workflow_rejects_invalid_plan_and_lifecycle_state() -> None:
    with pytest.raises(DomainValidationError, match="plan version 1"):
        replace(pending_workflow(), plan_version=2)

    with pytest.raises(DomainValidationError, match="frozen plan approval"):
        replace(pending_workflow(), status=WorkflowStatus.RUNNING)

    with pytest.raises(DomainValidationError, match="digest"):
        WorkflowPlanFingerprint(
            version=WORKFLOW_PLAN_FINGERPRINT_VERSION,
            digest="not-a-digest",
        )


def test_workflow_rejects_out_of_order_transition() -> None:
    with pytest.raises(InvalidStateTransition, match="expected discovery_running"):
        pending_workflow().begin_planning(at=NOW)


def test_tool_step_requires_immediate_predecessor_and_invocation() -> None:
    first = pending_tool_step()
    second = pending_tool_step(
        kind=WorkflowStepKind.MUTATION_TOOL,
        step_number=2,
        predecessor=first,
    )

    assert second.predecessor_step_id == first.id

    with pytest.raises(DomainValidationError, match="immediate predecessor"):
        replace(second, predecessor_step_number=None)

    with pytest.raises(DomainValidationError, match="invocation link"):
        replace(second, invocation_id=None)


def test_tool_step_lifecycle_records_known_and_unknown_failures() -> None:
    running = pending_tool_step().start(at=NOW + timedelta(seconds=1))

    failed = running.fail(
        at=NOW + timedelta(seconds=2),
        failure_code="temporarily_unavailable",
    )
    unknown = running.mark_unknown(
        at=NOW + timedelta(seconds=2),
        failure_code="dispatch_outcome_unknown",
    )

    assert failed.status is WorkflowStepStatus.FAILED
    assert unknown.status is WorkflowStepStatus.UNKNOWN


def test_pending_mutation_can_be_skipped_without_starting() -> None:
    skipped = pending_tool_step().skip(
        at=NOW + timedelta(seconds=1),
        failure_code="prior_mutation_failed",
    )

    assert skipped.status is WorkflowStepStatus.SKIPPED
    assert skipped.started_at is None


def test_final_response_requires_output_only_when_succeeded() -> None:
    final = WorkflowStep(
        id=WorkflowStepId(uuid4()),
        workflow_id=TurnWorkflowId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        step_number=1,
        kind=WorkflowStepKind.FINAL_RESPONSE,
        status=WorkflowStepStatus.PENDING,
        created_at=NOW,
    )
    running = final.start(at=NOW)

    with pytest.raises(DomainValidationError, match="output message"):
        running.succeed(at=NOW)

    succeeded = running.succeed(at=NOW, output_message_id=MessageId(uuid4()))
    assert succeeded.status is WorkflowStepStatus.SUCCEEDED


def test_terminal_step_cannot_transition_again() -> None:
    succeeded = pending_tool_step().start(at=NOW).succeed(at=NOW)

    with pytest.raises(InvalidStateTransition, match="cannot start"):
        succeeded.start(at=NOW)
