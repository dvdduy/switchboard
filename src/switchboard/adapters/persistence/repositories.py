"""SQLAlchemy implementations of application repository ports."""

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from switchboard.adapters.persistence.schema import (
    agent_definitions,
    agent_versions,
    conversations,
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
    message_from_record,
    message_to_record,
    turn_attempt_from_record,
    turn_attempt_to_record,
    turn_from_record,
    turn_to_record,
)
from switchboard.application.errors import ConversationNotFoundError
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.conversations import Conversation, Message, MessageRole
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    MessageId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt


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
