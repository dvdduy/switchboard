"""Repository contracts required by application use cases."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.conversations import Conversation, Message, MessageRole
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    ExecutionEventId,
    MessageId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt


class AgentRepository(Protocol):
    """Persistence operations for agents and immutable versions."""

    async def add_definition(
        self,
        definition: AgentDefinition,
    ) -> None:
        """Persist an agent definition."""

    async def add_version(
        self,
        version: AgentVersion,
    ) -> None:
        """Persist an immutable agent version."""

    async def get_definition(
        self,
        agent_definition_id: AgentDefinitionId,
    ) -> AgentDefinition | None:
        """Return an agent definition when it exists."""

    async def get_version(
        self,
        agent_version_id: AgentVersionId,
    ) -> AgentVersion | None:
        """Return an agent version when it exists."""


class ConversationRepository(Protocol):
    """Persistence operations for conversations and ordered messages."""

    async def add(
        self,
        conversation: Conversation,
    ) -> None:
        """Persist a new conversation."""

    async def get(
        self,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        """Return a conversation when it exists."""

    async def append_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
        role: MessageRole,
        content: str,
        created_at: datetime,
    ) -> Message:
        """Lock the conversation and append its next ordered message."""

    async def list_messages(
        self,
        conversation_id: ConversationId,
    ) -> tuple[Message, ...]:
        """Return committed messages in deterministic sequence order."""


class TurnRepository(Protocol):
    """Persistence operations for turns, attempts, and execution events."""

    async def add(
        self,
        turn: Turn,
    ) -> None:
        """Persist a new logical turn."""

    async def get(
        self,
        turn_id: TurnId,
    ) -> Turn | None:
        """Return a turn when it exists."""

    async def update_turn_lifecycle(
        self,
        *,
        previous: Turn,
        updated: Turn,
    ) -> None:
        """Persist a lifecycle transition using compare-and-set."""

    async def add_attempt(
        self,
        attempt: TurnAttempt,
    ) -> None:
        """Persist a new physical execution attempt."""

    async def get_attempt(
        self,
        attempt_id: TurnAttemptId,
    ) -> TurnAttempt | None:
        """Return a physical execution attempt when it exists."""

    async def update_attempt_lifecycle(
        self,
        *,
        previous: TurnAttempt,
        updated: TurnAttempt,
    ) -> None:
        """Persist an attempt transition using compare-and-set."""

    async def list_attempts(
        self,
        turn_id: TurnId,
    ) -> tuple[TurnAttempt, ...]:
        """Return attempts ordered by attempt number."""

    async def append_event(
        self,
        *,
        turn_id: TurnId,
        event_id: ExecutionEventId,
        attempt_id: TurnAttemptId | None,
        kind: ExecutionEventKind,
        payload: Mapping[str, object],
        occurred_at: datetime,
    ) -> ExecutionEvent:
        """Lock the turn and append its next durable event."""

    async def list_events(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
        limit: int,
    ) -> tuple[ExecutionEvent, ...]:
        """Return events after an exclusive sequence cursor."""
