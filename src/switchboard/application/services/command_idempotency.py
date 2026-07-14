"""Deterministic hashing for public command idempotency."""

import hashlib
import json

from switchboard.application.errors import InvalidIdempotencyKeyError
from switchboard.domain.command_receipts import (
    CREATE_CONVERSATION_SCOPE,
    ApprovalDecision,
    CommandOperation,
)
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    ApprovalRequestId,
    ConversationId,
    TeamId,
)

FINGERPRINT_VERSION = 1
MAX_IDEMPOTENCY_KEY_LENGTH = 128


def hash_idempotency_key(value: str) -> str:
    """Validate and hash one opaque key without normalizing its identity."""

    if not 1 <= len(value) <= MAX_IDEMPOTENCY_KEY_LENGTH or any(
        not 0x21 <= ord(character) <= 0x7E for character in value
    ):
        raise InvalidIdempotencyKeyError(
            "idempotency key must contain 1-128 visible ASCII characters"
        )
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def fingerprint_create_conversation(
    *,
    team_id: TeamId,
    agent_version_id: AgentVersionId,
    initial_user_message: str,
) -> str:
    """Fingerprint the validated semantic create-conversation request."""

    return _fingerprint(
        {
            "agent_version_id": str(agent_version_id),
            "command_scope": CREATE_CONVERSATION_SCOPE,
            "initial_user_message": initial_user_message,
            "operation": CommandOperation.CREATE_CONVERSATION.value,
            "team_id": str(team_id),
        }
    )


def fingerprint_continue_conversation(
    *,
    team_id: TeamId,
    conversation_id: ConversationId,
    user_message: str,
) -> str:
    """Fingerprint the validated semantic continue-conversation request."""

    return _fingerprint(
        {
            "command_scope": str(conversation_id),
            "conversation_id": str(conversation_id),
            "operation": CommandOperation.CONTINUE_CONVERSATION.value,
            "team_id": str(team_id),
            "user_message": user_message,
        }
    )


def fingerprint_approval_decision(
    *,
    team_id: TeamId,
    approval_id: ApprovalRequestId,
    actor_id: ActorId,
    decision: ApprovalDecision,
) -> str:
    """Fingerprint one exact actor-bound approval decision."""

    return _fingerprint(
        {
            "actor_id": str(actor_id),
            "approval_id": str(approval_id),
            "command_scope": str(approval_id),
            "decision": decision.value,
            "operation": CommandOperation.DECIDE_APPROVAL.value,
            "team_id": str(team_id),
        }
    )


def _fingerprint(fields: dict[str, object]) -> str:
    canonical = json.dumps(
        {"fingerprint_version": FINGERPRINT_VERSION, **fields},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
