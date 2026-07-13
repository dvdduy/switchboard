"""Conversation and immutable message domain entities."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import (
    normalize_utc,
    require_not_before,
    require_positive,
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


class ConversationStatus(StrEnum):
    """Lifecycle state of a conversation."""

    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(StrEnum):
    """Visible author role for a conversation message."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Conversation:
    """Long-lived container for an ordered conversation history."""

    id: ConversationId
    team_id: TeamId
    default_agent_version_id: AgentVersionId
    status: ConversationStatus
    next_message_sequence: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        created_at = normalize_utc(
            self.created_at,
            field_name="created_at",
        )
        updated_at = normalize_utc(
            self.updated_at,
            field_name="updated_at",
        )

        require_positive(
            self.next_message_sequence,
            field_name="next_message_sequence",
        )
        require_not_before(
            updated_at,
            minimum=created_at,
            field_name="updated_at",
            minimum_field_name="created_at",
        )

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    def allocate_message_sequence(
        self,
        *,
        at: datetime,
    ) -> tuple["Conversation", int]:
        """Allocate the next stable sequence for a new message."""

        if self.status is not ConversationStatus.ACTIVE:
            raise InvalidStateTransition("cannot append a message to a closed conversation")

        normalized_at = normalize_utc(
            at,
            field_name="at",
        )
        require_not_before(
            normalized_at,
            minimum=self.updated_at,
            field_name="at",
            minimum_field_name="updated_at",
        )

        allocated_sequence = self.next_message_sequence

        updated = replace(
            self,
            next_message_sequence=allocated_sequence + 1,
            updated_at=normalized_at,
        )

        return updated, allocated_sequence

    def close(self, *, at: datetime) -> "Conversation":
        """Close an active conversation."""

        if self.status is ConversationStatus.CLOSED:
            raise InvalidStateTransition("conversation is already closed")

        normalized_at = normalize_utc(at, field_name="at")
        require_not_before(
            normalized_at,
            minimum=self.updated_at,
            field_name="at",
            minimum_field_name="updated_at",
        )

        return replace(
            self,
            status=ConversationStatus.CLOSED,
            updated_at=normalized_at,
        )


@dataclass(frozen=True, slots=True)
class Message:
    """One immutable, ordered item in conversation history."""

    id: MessageId
    conversation_id: ConversationId
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_positive(self.sequence, field_name="sequence")

        if not self.content.strip():
            raise DomainValidationError("content must not be blank")

        object.__setattr__(
            self,
            "created_at",
            normalize_utc(
                self.created_at,
                field_name="created_at",
            ),
        )
