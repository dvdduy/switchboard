import hashlib
from uuid import uuid4

import pytest

from switchboard.application.errors import InvalidIdempotencyKeyError
from switchboard.application.services.command_idempotency import (
    fingerprint_approval_decision,
    fingerprint_continue_conversation,
    fingerprint_create_conversation,
    hash_idempotency_key,
)
from switchboard.domain.command_receipts import ApprovalDecision
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    ApprovalRequestId,
    ConversationId,
    TeamId,
)


def test_key_hash_is_exact_deterministic_and_does_not_retain_plaintext() -> None:
    key = "client-command-001"

    digest = hash_idempotency_key(key)

    assert digest == hashlib.sha256(key.encode("ascii")).hexdigest()
    assert key not in digest


@pytest.mark.parametrize("key", ["", "has space", "line\nbreak", "é", "x" * 129])
def test_key_hash_rejects_values_outside_bounded_visible_ascii(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        hash_idempotency_key(key)


def test_create_fingerprint_is_deterministic_and_sensitive_to_exact_content() -> None:
    team_id = TeamId(uuid4())
    agent_version_id = AgentVersionId(uuid4())

    first = fingerprint_create_conversation(
        team_id=team_id,
        agent_version_id=agent_version_id,
        initial_user_message="Hello",
    )
    replay = fingerprint_create_conversation(
        team_id=team_id,
        agent_version_id=agent_version_id,
        initial_user_message="Hello",
    )
    changed = fingerprint_create_conversation(
        team_id=team_id,
        agent_version_id=agent_version_id,
        initial_user_message="Hello ",
    )

    assert first == replay
    assert first != changed
    assert len(first) == 64


def test_continue_fingerprint_is_scoped_to_team_and_conversation() -> None:
    team_id = TeamId(uuid4())
    conversation_id = ConversationId(uuid4())
    baseline = fingerprint_continue_conversation(
        team_id=team_id,
        conversation_id=conversation_id,
        user_message="Continue",
    )

    assert baseline != fingerprint_continue_conversation(
        team_id=TeamId(uuid4()),
        conversation_id=conversation_id,
        user_message="Continue",
    )
    assert baseline != fingerprint_continue_conversation(
        team_id=team_id,
        conversation_id=ConversationId(uuid4()),
        user_message="Continue",
    )


def test_approval_fingerprint_changes_with_actor_or_decision() -> None:
    team_id = TeamId(uuid4())
    approval_id = ApprovalRequestId(uuid4())
    actor_id = ActorId(uuid4())
    baseline = fingerprint_approval_decision(
        team_id=team_id,
        approval_id=approval_id,
        actor_id=actor_id,
        decision=ApprovalDecision.APPROVE,
    )

    assert baseline != fingerprint_approval_decision(
        team_id=team_id,
        approval_id=approval_id,
        actor_id=ActorId(uuid4()),
        decision=ApprovalDecision.APPROVE,
    )
    assert baseline != fingerprint_approval_decision(
        team_id=team_id,
        approval_id=approval_id,
        actor_id=actor_id,
        decision=ApprovalDecision.REJECT,
    )
