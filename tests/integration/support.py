from datetime import datetime
from uuid import uuid4

from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.conversations import Conversation, ConversationStatus, MessageRole
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import (
    Turn,
    TurnAttempt,
    TurnAttemptStatus,
    TurnStatus,
)


async def seed_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    now: datetime,
) -> tuple[Turn, TurnAttempt]:
    team_id = TeamId(uuid4())

    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Project Assistant",
        created_at=now,
    )

    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=definition.id,
        version_number=1,
        created_at=now,
    )

    conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id,
        default_agent_version_id=version.id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=1,
        created_at=now,
        updated_at=now,
    )

    turn_id = TurnId(uuid4())

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(definition)
        await unit_of_work.agents.add_version(version)
        await unit_of_work.conversations.add(conversation)

        message = await unit_of_work.conversations.append_message(
            conversation_id=conversation.id,
            message_id=MessageId(uuid4()),
            role=MessageRole.USER,
            content="Show overdue work.",
            created_at=now,
        )

        turn = Turn(
            id=turn_id,
            conversation_id=conversation.id,
            input_message_id=message.id,
            agent_version_id=version.id,
            status=TurnStatus.RECEIVED,
            created_at=now,
        )

        attempt = TurnAttempt(
            id=TurnAttemptId(uuid4()),
            turn_id=turn.id,
            attempt_number=1,
            status=TurnAttemptStatus.PENDING,
            created_at=now,
        )

        await unit_of_work.turns.add(turn)
        await unit_of_work.turns.add_attempt(attempt)
        await unit_of_work.commit()

    return turn, attempt


async def seed_running_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    now: datetime,
) -> tuple[Turn, TurnAttempt]:
    """Seed a turn and attempt after their atomic start transition."""

    turn, attempt = await seed_turn(unit_of_work_factory, now=now)
    running_turn = turn.start()
    running_attempt = attempt.start(at=now)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.update_turn_lifecycle(
            previous=turn,
            updated=running_turn,
        )
        await unit_of_work.turns.update_attempt_lifecycle(
            previous=attempt,
            updated=running_attempt,
        )
        await unit_of_work.commit()

    return running_turn, running_attempt
