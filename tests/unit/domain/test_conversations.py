from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.errors import (
    DomainValidationError,
    InvalidStateTransition,
)
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
)


def make_conversation() -> Conversation:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)

    return Conversation(
        id=ConversationId(uuid4()),
        team_id=TeamId(uuid4()),
        default_agent_version_id=AgentVersionId(uuid4()),
        status=ConversationStatus.ACTIVE,
        next_message_sequence=1,
        created_at=now,
        updated_at=now,
    )


def test_conversation_allocates_sequences_without_mutating_original() -> None:
    conversation = make_conversation()
    later = conversation.updated_at + timedelta(seconds=1)

    updated, sequence = conversation.allocate_message_sequence(at=later)

    assert sequence == 1
    assert conversation.next_message_sequence == 1
    assert updated.next_message_sequence == 2
    assert updated.updated_at == later


def test_closed_conversation_rejects_new_messages() -> None:
    conversation = make_conversation()
    closed = conversation.close(at=conversation.updated_at + timedelta(seconds=1))

    with pytest.raises(
        InvalidStateTransition,
        match="cannot append a message",
    ):
        closed.allocate_message_sequence(at=closed.updated_at + timedelta(seconds=1))


def test_message_requires_positive_sequence() -> None:
    with pytest.raises(
        DomainValidationError,
        match="sequence must be greater than zero",
    ):
        Message(
            id=MessageId(uuid4()),
            conversation_id=ConversationId(uuid4()),
            sequence=0,
            role=MessageRole.USER,
            content="Hello",
            created_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_message_rejects_blank_content() -> None:
    with pytest.raises(
        DomainValidationError,
        match="content must not be blank",
    ):
        Message(
            id=MessageId(uuid4()),
            conversation_id=ConversationId(uuid4()),
            sequence=1,
            role=MessageRole.USER,
            content="   ",
            created_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_domain_rejects_naive_datetime() -> None:
    with pytest.raises(
        DomainValidationError,
        match="created_at must be timezone-aware",
    ):
        Message(
            id=MessageId(uuid4()),
            conversation_id=ConversationId(uuid4()),
            sequence=1,
            role=MessageRole.USER,
            content="Hello",
            created_at=datetime(2026, 7, 13),
        )
