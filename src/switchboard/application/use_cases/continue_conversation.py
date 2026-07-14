"""Application workflow for appending a durable user turn."""

from dataclasses import dataclass

from switchboard.application.errors import (
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationTeamMismatchError,
    IdempotencyConflictError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.application.services.command_idempotency import (
    fingerprint_continue_conversation,
    hash_idempotency_key,
)
from switchboard.domain.command_receipts import CommandOperation, CommandReceipt
from switchboard.domain.conversations import ConversationStatus, MessageRole
from switchboard.domain.identifiers import (
    CommandReceiptId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus


@dataclass(frozen=True, slots=True)
class ContinueConversationCommand:
    """Input required to append one user turn to an active conversation."""

    team_id: TeamId
    conversation_id: ConversationId
    user_message: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ContinueConversationResult:
    """Stable identities accepted for one continued turn."""

    conversation_id: ConversationId
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId


class ContinueConversation:
    """Append one message, logical turn, and pending attempt atomically."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        message_ids: IdGenerator[MessageId],
        turn_ids: IdGenerator[TurnId],
        attempt_ids: IdGenerator[TurnAttemptId],
        receipt_ids: IdGenerator[CommandReceiptId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._message_ids = message_ids
        self._turn_ids = turn_ids
        self._attempt_ids = attempt_ids
        self._receipt_ids = receipt_ids

    async def execute(
        self,
        command: ContinueConversationCommand,
    ) -> ContinueConversationResult:
        """Execute one atomic, durably idempotent continuation."""

        idempotency_key_hash = hash_idempotency_key(command.idempotency_key)
        request_fingerprint = fingerprint_continue_conversation(
            team_id=command.team_id,
            conversation_id=command.conversation_id,
            user_message=command.user_message,
        )
        created_at = self._clock.now()
        message_id = self._message_ids.new()
        turn_id = self._turn_ids.new()
        attempt_id = self._attempt_ids.new()
        receipt = CommandReceipt(
            id=self._receipt_ids.new(),
            team_id=command.team_id,
            operation=CommandOperation.CONTINUE_CONVERSATION,
            command_scope=str(command.conversation_id),
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            conversation_id=command.conversation_id,
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

            conversation = await unit_of_work.conversations.get(command.conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(
                    f"conversation {command.conversation_id} was not found"
                )
            if conversation.team_id != command.team_id:
                raise ConversationTeamMismatchError(
                    f"conversation does not belong to team {command.team_id}"
                )
            if conversation.status is ConversationStatus.CLOSED:
                raise ConversationClosedError("conversation is closed")

            message = await unit_of_work.conversations.append_message(
                conversation_id=conversation.id,
                message_id=message_id,
                role=MessageRole.USER,
                content=command.user_message,
                created_at=created_at,
            )
            await unit_of_work.turns.add(
                Turn(
                    id=turn_id,
                    conversation_id=conversation.id,
                    input_message_id=message.id,
                    agent_version_id=conversation.default_agent_version_id,
                    status=TurnStatus.RECEIVED,
                    created_at=created_at,
                )
            )
            await unit_of_work.turns.add_attempt(
                TurnAttempt(
                    id=attempt_id,
                    turn_id=turn_id,
                    attempt_number=1,
                    status=TurnAttemptStatus.PENDING,
                    created_at=created_at,
                )
            )
            await unit_of_work.commit()

        return _result_from_receipt(receipt)


def _result_from_receipt(receipt: CommandReceipt) -> ContinueConversationResult:
    return ContinueConversationResult(
        conversation_id=receipt.conversation_id,
        message_id=receipt.message_id,
        turn_id=receipt.turn_id,
        attempt_id=receipt.attempt_id,
    )
