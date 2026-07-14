"""Framework-independent read services for the public conversation contract."""

from dataclasses import dataclass
from datetime import datetime

from switchboard.application.errors import (
    ConversationNotFoundError,
    ConversationTeamMismatchError,
    PaginationValidationError,
    TurnNotFoundError,
    TurnTeamMismatchError,
)
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.conversations import Conversation, ConversationStatus, Message, MessageRole
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus

DEFAULT_MESSAGE_PAGE_LIMIT = 50
MAX_MESSAGE_PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ConversationReadModel:
    conversation_id: ConversationId
    default_agent_version_id: AgentVersionId
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageReadModel:
    message_id: MessageId
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: tuple[MessageReadModel, ...]
    next_after_sequence: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class TurnAttemptReadModel:
    attempt_number: int
    status: TurnAttemptStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TurnReadModel:
    turn_id: TurnId
    conversation_id: ConversationId
    input_message_id: MessageId
    agent_version_id: AgentVersionId
    status: TurnStatus
    created_at: datetime
    completed_at: datetime | None
    attempts: tuple[TurnAttemptReadModel, ...]


class GetConversation:
    """Read safe metadata for one team-owned conversation."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        *,
        team_id: TeamId,
        conversation_id: ConversationId,
    ) -> ConversationReadModel:
        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.conversations.get(conversation_id)
        _require_conversation_owner(
            conversation,
            team_id=team_id,
            conversation_id=conversation_id,
        )
        assert conversation is not None
        return _conversation_read_model(conversation)


class ListConversationMessages:
    """Read one bounded deterministic page of visible conversation history."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        *,
        team_id: TeamId,
        conversation_id: ConversationId,
        after_sequence: int = 0,
        limit: int = DEFAULT_MESSAGE_PAGE_LIMIT,
    ) -> MessagePage:
        _validate_pagination(after_sequence=after_sequence, limit=limit)

        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.conversations.get(conversation_id)
            _require_conversation_owner(
                conversation,
                team_id=team_id,
                conversation_id=conversation_id,
            )
            messages = await unit_of_work.conversations.list_messages_after(
                conversation_id=conversation_id,
                after_sequence=after_sequence,
                limit=limit + 1,
            )

        page_messages = messages[:limit]
        items = tuple(_message_read_model(message) for message in page_messages)
        next_cursor = items[-1].sequence if items else after_sequence
        return MessagePage(
            items=items,
            next_after_sequence=next_cursor,
            has_more=len(messages) > limit,
        )


class GetTurn:
    """Read safe logical-turn and physical-attempt state."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        *,
        team_id: TeamId,
        turn_id: TurnId,
    ) -> TurnReadModel:
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(turn_id)
            if turn is None:
                raise TurnNotFoundError(f"turn {turn_id} was not found")
            conversation = await unit_of_work.conversations.get(turn.conversation_id)
            if conversation is None or conversation.team_id != team_id:
                raise TurnTeamMismatchError(f"turn does not belong to team {team_id}")
            attempts = await unit_of_work.turns.list_attempts(turn_id)

        return _turn_read_model(turn, attempts)


def _validate_pagination(*, after_sequence: int, limit: int) -> None:
    if after_sequence < 0:
        raise PaginationValidationError("after_sequence must not be negative")
    if not 1 <= limit <= MAX_MESSAGE_PAGE_LIMIT:
        raise PaginationValidationError(f"limit must be between 1 and {MAX_MESSAGE_PAGE_LIMIT}")


def _require_conversation_owner(
    conversation: Conversation | None,
    *,
    team_id: TeamId,
    conversation_id: ConversationId,
) -> None:
    if conversation is None:
        raise ConversationNotFoundError(f"conversation {conversation_id} was not found")
    if conversation.team_id != team_id:
        raise ConversationTeamMismatchError(f"conversation does not belong to team {team_id}")


def _conversation_read_model(conversation: Conversation) -> ConversationReadModel:
    return ConversationReadModel(
        conversation_id=conversation.id,
        default_agent_version_id=conversation.default_agent_version_id,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_read_model(message: Message) -> MessageReadModel:
    return MessageReadModel(
        message_id=message.id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


def _turn_read_model(turn: Turn, attempts: tuple[TurnAttempt, ...]) -> TurnReadModel:
    return TurnReadModel(
        turn_id=turn.id,
        conversation_id=turn.conversation_id,
        input_message_id=turn.input_message_id,
        agent_version_id=turn.agent_version_id,
        status=turn.status,
        created_at=turn.created_at,
        completed_at=turn.completed_at,
        attempts=tuple(
            TurnAttemptReadModel(
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                created_at=attempt.created_at,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
            )
            for attempt in attempts
        ),
    )
