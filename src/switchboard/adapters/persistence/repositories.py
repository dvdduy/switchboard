"""SQLAlchemy implementations of application repository ports."""

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from switchboard.adapters.persistence.schema import (
    agent_definitions,
    agent_versions,
    conversations,
    execution_events,
    messages,
    turn_attempts,
    turns,
)
from switchboard.adapters.persistence.translators import (
    agent_definition_from_record,
    agent_definition_to_record,
    agent_version_from_record,
    agent_version_to_record,
    conversation_from_record,
    conversation_to_record,
    execution_event_from_record,
    execution_event_to_record,
    message_from_record,
    message_to_record,
    turn_attempt_from_record,
    turn_attempt_to_record,
    turn_from_record,
    turn_to_record,
)
from switchboard.application.errors import (
    ConversationNotFoundError,
    TurnAttemptLifecycleConflictError,
    TurnEventStateError,
    TurnLifecycleConflictError,
    TurnNotFoundError,
)
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
from switchboard.domain.turns import Turn, TurnAttempt, TurnStatus


def _matches_nullable(
    column: ColumnElement[object],
    value: object,
) -> ColumnElement[bool]:
    if value is None:
        return column.is_(None)

    return column == value


class SqlAlchemyAgentRepository:
    """Persists agent definitions and immutable versions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_definition(
        self,
        definition: AgentDefinition,
    ) -> None:
        await self._session.execute(
            insert(agent_definitions).values(agent_definition_to_record(definition))
        )

    async def add_version(
        self,
        version: AgentVersion,
    ) -> None:
        await self._session.execute(insert(agent_versions).values(agent_version_to_record(version)))

    async def get_version(
        self,
        agent_version_id: AgentVersionId,
    ) -> AgentVersion | None:
        statement = select(agent_versions).where(agent_versions.c.id == agent_version_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return agent_version_from_record(record)

    async def get_definition(
        self,
        agent_definition_id: AgentDefinitionId,
    ) -> AgentDefinition | None:
        statement = select(agent_definitions).where(agent_definitions.c.id == agent_definition_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return agent_definition_from_record(record)


class SqlAlchemyConversationRepository:
    """Persists conversations and allocates ordered messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        conversation: Conversation,
    ) -> None:
        await self._session.execute(
            insert(conversations).values(conversation_to_record(conversation))
        )

    async def get(
        self,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        statement = select(conversations).where(conversations.c.id == conversation_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return conversation_from_record(record)

    async def append_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
        role: MessageRole,
        content: str,
        created_at: datetime,
    ) -> Message:
        statement = (
            select(conversations).where(conversations.c.id == conversation_id).with_for_update()
        )

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            raise ConversationNotFoundError(f"conversation {conversation_id} was not found")

        conversation = conversation_from_record(record)

        updated_conversation, allocated_sequence = conversation.allocate_message_sequence(
            at=created_at
        )

        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            sequence=allocated_sequence,
            role=role,
            content=content,
            created_at=created_at,
        )

        await self._session.execute(
            update(conversations)
            .where(conversations.c.id == conversation_id)
            .values(
                next_message_sequence=(updated_conversation.next_message_sequence),
                updated_at=updated_conversation.updated_at,
            )
        )

        await self._session.execute(insert(messages).values(message_to_record(message)))

        return message

    async def list_messages(
        self,
        conversation_id: ConversationId,
    ) -> tuple[Message, ...]:
        statement = (
            select(messages)
            .where(messages.c.conversation_id == conversation_id)
            .order_by(messages.c.sequence)
        )

        result = await self._session.execute(statement)

        return tuple(message_from_record(record) for record in result.mappings())


class SqlAlchemyTurnRepository:
    """Persists logical turns and physical attempts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        turn: Turn,
    ) -> None:
        await self._session.execute(insert(turns).values(turn_to_record(turn)))

    async def get(
        self,
        turn_id: TurnId,
    ) -> Turn | None:
        statement = select(turns).where(turns.c.id == turn_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return turn_from_record(record)

    async def add_attempt(
        self,
        attempt: TurnAttempt,
    ) -> None:
        await self._session.execute(insert(turn_attempts).values(turn_attempt_to_record(attempt)))

    async def list_attempts(
        self,
        turn_id: TurnId,
    ) -> tuple[TurnAttempt, ...]:
        statement = (
            select(turn_attempts)
            .where(turn_attempts.c.turn_id == turn_id)
            .order_by(turn_attempts.c.attempt_number)
        )

        result = await self._session.execute(statement)

        return tuple(turn_attempt_from_record(record) for record in result.mappings())

    async def update_turn_lifecycle(
        self,
        *,
        previous: Turn,
        updated: Turn,
    ) -> None:
        if previous.id != updated.id:
            raise ValueError("turn lifecycle transition must preserve identity")

        result = await self._session.execute(
            update(turns)
            .where(
                turns.c.id == previous.id,
                turns.c.status == previous.status.value,
                _matches_nullable(
                    turns.c.completed_at,
                    previous.completed_at,
                ),
            )
            .values(
                status=updated.status.value,
                completed_at=updated.completed_at,
            )
            .returning(turns.c.id)
        )

        updated_id = result.scalar_one_or_none()

        if updated_id is None:
            raise TurnLifecycleConflictError(
                f"turn {previous.id} lifecycle changed after it was read"
            )

    async def get_attempt(
        self,
        attempt_id: TurnAttemptId,
    ) -> TurnAttempt | None:
        statement = select(turn_attempts).where(turn_attempts.c.id == attempt_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return turn_attempt_from_record(record)

    async def update_attempt_lifecycle(
        self,
        *,
        previous: TurnAttempt,
        updated: TurnAttempt,
    ) -> None:
        if previous.id != updated.id:
            raise ValueError("attempt lifecycle transition must preserve identity")

        result = await self._session.execute(
            update(turn_attempts)
            .where(
                turn_attempts.c.id == previous.id,
                turn_attempts.c.status == previous.status.value,
                _matches_nullable(
                    turn_attempts.c.started_at,
                    previous.started_at,
                ),
                _matches_nullable(
                    turn_attempts.c.completed_at,
                    previous.completed_at,
                ),
                _matches_nullable(
                    turn_attempts.c.failure_code,
                    previous.failure_code,
                ),
            )
            .values(
                status=updated.status.value,
                started_at=updated.started_at,
                completed_at=updated.completed_at,
                failure_code=updated.failure_code,
            )
            .returning(turn_attempts.c.id)
        )

        updated_id = result.scalar_one_or_none()

        if updated_id is None:
            raise TurnAttemptLifecycleConflictError(
                f"turn attempt {previous.id} lifecycle changed after it was read"
            )

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
        statement = select(turns).where(turns.c.id == turn_id).with_for_update()

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            raise TurnNotFoundError(f"turn {turn_id} was not found")

        turn = turn_from_record(record)

        if turn.status is TurnStatus.RECEIVED:
            raise TurnEventStateError(f"turn {turn.id} has not started")

        if turn.status is TurnStatus.RUNNING:
            allowed_kinds = {
                ExecutionEventKind.TURN_STARTED,
                ExecutionEventKind.RESPONSE_DELTA,
            }
        elif turn.status is TurnStatus.COMPLETED:
            allowed_kinds = {
                ExecutionEventKind.TURN_COMPLETED,
            }
        elif turn.status is TurnStatus.FAILED:
            allowed_kinds = {
                ExecutionEventKind.TURN_FAILED,
            }
        else:
            allowed_kinds = set()

        if kind not in allowed_kinds:
            raise TurnEventStateError(
                f"event {kind.value} is not valid for turn status {turn.status.value}"
            )

        updated_turn, allocated_sequence = turn.allocate_event_sequence()

        event = ExecutionEvent(
            id=event_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            sequence=allocated_sequence,
            kind=kind,
            payload=payload,
            occurred_at=occurred_at,
        )

        if event.occurred_at < turn.created_at:
            raise ValueError("execution event cannot occur before its turn")

        await self._session.execute(
            update(turns)
            .where(turns.c.id == turn_id)
            .values(next_event_sequence=(updated_turn.next_event_sequence))
        )

        await self._session.execute(
            insert(execution_events).values(execution_event_to_record(event))
        )

        return event

    async def list_events(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
        limit: int,
    ) -> tuple[ExecutionEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")

        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        statement = (
            select(execution_events)
            .where(
                execution_events.c.turn_id == turn_id,
                execution_events.c.sequence > after_sequence,
            )
            .order_by(execution_events.c.sequence)
            .limit(limit)
        )

        result = await self._session.execute(statement)

        return tuple(execution_event_from_record(record) for record in result.mappings())
