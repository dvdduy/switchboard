"""Application workflow for starting a durable conversation."""

from dataclasses import dataclass

from switchboard.application.errors import (
    AgentDefinitionNotFoundError,
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
    IdempotencyConflictError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.application.services.command_idempotency import (
    fingerprint_create_conversation,
    hash_idempotency_key,
)
from switchboard.domain.command_receipts import (
    CREATE_CONVERSATION_SCOPE,
    CommandOperation,
    CommandReceipt,
)
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    MessageRole,
)
from switchboard.domain.identifiers import (
    AgentVersionId,
    CommandReceiptId,
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
    idempotency_key: str


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
        receipt_ids: IdGenerator[CommandReceiptId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._conversation_ids = conversation_ids
        self._message_ids = message_ids
        self._turn_ids = turn_ids
        self._attempt_ids = attempt_ids
        self._receipt_ids = receipt_ids

    async def execute(
        self,
        command: StartConversationCommand,
    ) -> StartConversationResult:
        """Execute the atomic start-conversation transaction."""

        idempotency_key_hash = hash_idempotency_key(command.idempotency_key)
        request_fingerprint = fingerprint_create_conversation(
            team_id=command.team_id,
            agent_version_id=command.agent_version_id,
            initial_user_message=command.initial_user_message,
        )
        created_at = self._clock.now()
        conversation_id = self._conversation_ids.new()
        message_id = self._message_ids.new()
        turn_id = self._turn_ids.new()
        attempt_id = self._attempt_ids.new()
        receipt = CommandReceipt(
            id=self._receipt_ids.new(),
            team_id=command.team_id,
            operation=CommandOperation.CREATE_CONVERSATION,
            command_scope=CREATE_CONVERSATION_SCOPE,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            conversation_id=conversation_id,
            message_id=message_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            created_at=created_at,
        )

        async with self._unit_of_work_factory() as unit_of_work:
            authority, created = await unit_of_work.command_receipts.add_or_get(receipt)
            if not authority.has_same_request(request_fingerprint):
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )
            if not created:
                return _result_from_receipt(authority)

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

        return _result_from_receipt(receipt)


def _result_from_receipt(receipt: CommandReceipt) -> StartConversationResult:
    if any(
        value is None
        for value in (
            receipt.conversation_id,
            receipt.message_id,
            receipt.turn_id,
            receipt.attempt_id,
        )
    ):
        raise RuntimeError("conversation receipt is missing result identities")
    assert receipt.conversation_id is not None
    assert receipt.message_id is not None
    assert receipt.turn_id is not None
    assert receipt.attempt_id is not None
    return StartConversationResult(
        conversation_id=receipt.conversation_id,
        message_id=receipt.message_id,
        turn_id=receipt.turn_id,
        attempt_id=receipt.attempt_id,
    )
