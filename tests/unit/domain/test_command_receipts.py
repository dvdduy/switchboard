from datetime import UTC, datetime
from uuid import uuid4

import pytest

from switchboard.domain.command_receipts import (
    CREATE_CONVERSATION_SCOPE,
    ApprovalDecision,
    CommandOperation,
    CommandReceipt,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    ActorId,
    ApprovalRequestId,
    CommandReceiptId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)

NOW = datetime(2026, 7, 14, 18, 0, tzinfo=UTC)


def receipt(
    *,
    operation: CommandOperation = CommandOperation.CREATE_CONVERSATION,
    command_scope: str = CREATE_CONVERSATION_SCOPE,
    conversation_id: ConversationId | None = None,
) -> CommandReceipt:
    resolved_conversation_id = conversation_id or ConversationId(uuid4())
    return CommandReceipt(
        id=CommandReceiptId(uuid4()),
        team_id=TeamId(uuid4()),
        operation=operation,
        command_scope=command_scope,
        idempotency_key_hash="a" * 64,
        request_fingerprint="b" * 64,
        conversation_id=resolved_conversation_id,
        message_id=MessageId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        created_at=NOW,
    )


def test_create_receipt_uses_fixed_scope_and_matches_exact_fingerprint() -> None:
    command_receipt = receipt()

    assert command_receipt.command_scope == CREATE_CONVERSATION_SCOPE
    assert command_receipt.has_same_request("b" * 64)
    assert not command_receipt.has_same_request("c" * 64)


def test_continue_receipt_scope_is_its_result_conversation() -> None:
    conversation_id = ConversationId(uuid4())
    command_receipt = receipt(
        operation=CommandOperation.CONTINUE_CONVERSATION,
        command_scope=str(conversation_id),
        conversation_id=conversation_id,
    )

    assert command_receipt.command_scope == str(conversation_id)


def test_approval_receipt_contains_only_actor_bound_decision_result() -> None:
    approval_id = ApprovalRequestId(uuid4())
    actor_id = ActorId(uuid4())

    command_receipt = CommandReceipt(
        id=CommandReceiptId(uuid4()),
        team_id=TeamId(uuid4()),
        operation=CommandOperation.DECIDE_APPROVAL,
        command_scope=str(approval_id),
        idempotency_key_hash="a" * 64,
        request_fingerprint="b" * 64,
        created_at=NOW,
        approval_id=approval_id,
        actor_id=actor_id,
        approval_decision=ApprovalDecision.APPROVE,
    )

    assert command_receipt.approval_id == approval_id
    assert command_receipt.actor_id == actor_id
    assert command_receipt.conversation_id is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("idempotency_key_hash", "A" * 64),
        ("idempotency_key_hash", "a" * 63),
        ("request_fingerprint", "g" * 64),
    ],
)
def test_receipt_rejects_invalid_sha256_digests(field: str, value: str) -> None:
    values = {
        "idempotency_key_hash": "a" * 64,
        "request_fingerprint": "b" * 64,
    }
    values[field] = value

    with pytest.raises(DomainValidationError, match=field):
        CommandReceipt(
            id=CommandReceiptId(uuid4()),
            team_id=TeamId(uuid4()),
            operation=CommandOperation.CREATE_CONVERSATION,
            command_scope=CREATE_CONVERSATION_SCOPE,
            idempotency_key_hash=values["idempotency_key_hash"],
            request_fingerprint=values["request_fingerprint"],
            conversation_id=ConversationId(uuid4()),
            message_id=MessageId(uuid4()),
            turn_id=TurnId(uuid4()),
            attempt_id=TurnAttemptId(uuid4()),
            created_at=NOW,
        )


def test_receipt_rejects_scope_that_does_not_match_operation() -> None:
    with pytest.raises(DomainValidationError, match="command_scope"):
        receipt(command_scope=str(uuid4()))
