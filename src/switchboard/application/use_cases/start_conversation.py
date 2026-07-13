"""Application workflow for starting a durable conversation."""

from dataclasses import dataclass

from switchboard.application.errors import (
    AgentDefinitionNotFoundError,
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    MessageRole,
)
from switchboard.domain.identifiers import (
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


@dataclass(frozen=True, slots=True)
class StartConversationCommand:
    """Input required to create a new conversation and first turn."""

    team_id: TeamId
    agent_version_id: AgentVersionId
    initial_user_message: str


@dataclass(frozen=True, slots=True)
class StartConversationResult:
    """Stable identities created by the workflow."""

    conversation_id: ConversationId
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId


class StartConversation:
    """Create the conversation and its first durable turn atomically."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        conversation_ids: IdGenerator[ConversationId],
        message_ids: IdGenerator[MessageId],
        turn_ids: IdGenerator[TurnId],
        attempt_ids: IdGenerator[TurnAttemptId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._conversation_ids = conversation_ids
        self._message_ids = message_ids
        self._turn_ids = turn_ids
        self._attempt_ids = attempt_ids

    async def execute(
        self,
        command: StartConversationCommand,
    ) -> StartConversationResult:
        """Execute the atomic start-conversation transaction."""

        async with self._unit_of_work_factory() as unit_of_work:
            agent_version = await unit_of_work.agents.get_version(command.agent_version_id)

            if agent_version is None:
                raise AgentVersionNotFoundError(
                    f"agent version {command.agent_version_id} was not found"
                )

            agent_definition = await unit_of_work.agents.get_definition(
                agent_version.agent_definition_id
            )

            if agent_definition is None:
                raise AgentDefinitionNotFoundError(
                    f"agent definition {agent_version.agent_definition_id} was not found"
                )

            if agent_definition.team_id != command.team_id:
                raise AgentTeamMismatchError(
                    f"selected agent version does not belong to team {command.team_id}"
                )

            created_at = self._clock.now()

            conversation_id = self._conversation_ids.new()
            message_id = self._message_ids.new()
            turn_id = self._turn_ids.new()
            attempt_id = self._attempt_ids.new()

            conversation = Conversation(
                id=conversation_id,
                team_id=command.team_id,
                default_agent_version_id=command.agent_version_id,
                status=ConversationStatus.ACTIVE,
                next_message_sequence=1,
                created_at=created_at,
                updated_at=created_at,
            )

            await unit_of_work.conversations.add(conversation)

            message = await unit_of_work.conversations.append_message(
                conversation_id=conversation_id,
                message_id=message_id,
                role=MessageRole.USER,
                content=command.initial_user_message,
                created_at=created_at,
            )

            turn = Turn(
                id=turn_id,
                conversation_id=conversation_id,
                input_message_id=message.id,
                agent_version_id=command.agent_version_id,
                status=TurnStatus.RECEIVED,
                created_at=created_at,
            )

            attempt = TurnAttempt(
                id=attempt_id,
                turn_id=turn_id,
                attempt_number=1,
                status=TurnAttemptStatus.PENDING,
                created_at=created_at,
            )

            await unit_of_work.turns.add(turn)
            await unit_of_work.turns.add_attempt(attempt)

            await unit_of_work.commit()

        return StartConversationResult(
            conversation_id=conversation_id,
            message_id=message_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
        )
