"""Immutable durable receipts for idempotent public commands."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import normalize_utc
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    CommandReceiptId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)

CREATE_CONVERSATION_SCOPE = "create"


class CommandOperation(StrEnum):
    """Stable operation names participating in idempotency authority."""

    CREATE_CONVERSATION = "create_conversation"
    CONTINUE_CONVERSATION = "continue_conversation"


def _require_sha256(value: str, *, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise DomainValidationError(f"{field_name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    """Committed authority and result for one idempotent command scope."""

    id: CommandReceiptId
    team_id: TeamId
    operation: CommandOperation
    command_scope: str
    idempotency_key_hash: str
    request_fingerprint: str
    conversation_id: ConversationId
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    created_at: datetime

    def __post_init__(self) -> None:
        expected_scope = (
            CREATE_CONVERSATION_SCOPE
            if self.operation is CommandOperation.CREATE_CONVERSATION
            else str(self.conversation_id)
        )
        if self.command_scope != expected_scope:
            raise DomainValidationError(
                f"command_scope must be {expected_scope!r} for {self.operation.value}"
            )

        _require_sha256(self.idempotency_key_hash, field_name="idempotency_key_hash")
        _require_sha256(self.request_fingerprint, field_name="request_fingerprint")
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(self.created_at, field_name="created_at"),
        )

    def has_same_request(self, request_fingerprint: str) -> bool:
        """Return whether a replay represents the exact accepted request."""

        _require_sha256(request_fingerprint, field_name="request_fingerprint")
        return self.request_fingerprint == request_fingerprint
