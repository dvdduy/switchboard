"""Durable logical turns and their physical execution attempts."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import (
    normalize_utc,
    require_not_before,
    require_not_blank,
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
    TurnAttemptId,
    TurnId,
)


class TurnStatus(StrEnum):
    """Lifecycle state of one logical user request."""

    RECEIVED = "received"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TurnAttemptStatus(StrEnum):
    """Lifecycle state of one physical processing attempt."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TURN_TERMINAL_STATUSES = frozenset(
    {
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.CANCELLED,
    }
)


_ATTEMPT_TERMINAL_STATUSES = frozenset(
    {
        TurnAttemptStatus.SUCCEEDED,
        TurnAttemptStatus.FAILED,
        TurnAttemptStatus.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class Turn:
    """One logical request originating from one input message."""

    id: TurnId
    conversation_id: ConversationId
    input_message_id: MessageId
    agent_version_id: AgentVersionId
    status: TurnStatus
    created_at: datetime
    next_event_sequence: int = 1
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_positive(
            self.next_event_sequence,
            field_name="next_event_sequence",
        )

        created_at = normalize_utc(
            self.created_at,
            field_name="created_at",
        )
        object.__setattr__(self, "created_at", created_at)

        if self.completed_at is not None:
            completed_at = normalize_utc(
                self.completed_at,
                field_name="completed_at",
            )
            require_not_before(
                completed_at,
                minimum=created_at,
                field_name="completed_at",
                minimum_field_name="created_at",
            )
            object.__setattr__(
                self,
                "completed_at",
                completed_at,
            )

        if self.status in _TURN_TERMINAL_STATUSES:
            if self.completed_at is None:
                raise DomainValidationError("terminal turn status requires completed_at")
        elif self.completed_at is not None:
            raise DomainValidationError("non-terminal turn status must not have completed_at")

    @property
    def is_terminal(self) -> bool:
        """Return whether no further execution work may occur."""

        return self.status in _TURN_TERMINAL_STATUSES

    def allocate_event_sequence(
        self,
    ) -> tuple["Turn", int]:
        """Allocate the next deterministic event sequence."""

        allocated_sequence = self.next_event_sequence

        return (
            replace(
                self,
                next_event_sequence=allocated_sequence + 1,
            ),
            allocated_sequence,
        )

    def start(self) -> "Turn":
        """Move a received turn into active processing."""

        if self.status is not TurnStatus.RECEIVED:
            raise InvalidStateTransition(f"cannot start a turn from {self.status.value}")

        return replace(self, status=TurnStatus.RUNNING)

    def complete(self, *, at: datetime) -> "Turn":
        """Complete a running turn."""

        if self.status is not TurnStatus.RUNNING:
            raise InvalidStateTransition(f"cannot complete a turn from {self.status.value}")

        return replace(
            self,
            status=TurnStatus.COMPLETED,
            completed_at=normalize_utc(at, field_name="at"),
        )

    def await_confirmation(self) -> "Turn":
        """Pause a running turn without making it terminal."""

        if self.status is not TurnStatus.RUNNING:
            raise InvalidStateTransition(f"cannot await confirmation from {self.status.value}")
        return replace(self, status=TurnStatus.AWAITING_CONFIRMATION)

    def resume(self) -> "Turn":
        """Resume a durably paused turn after approval consumption."""

        if self.status is not TurnStatus.AWAITING_CONFIRMATION:
            raise InvalidStateTransition(f"cannot resume a turn from {self.status.value}")
        return replace(self, status=TurnStatus.RUNNING)

    def fail(self, *, at: datetime) -> "Turn":
        """Fail a received or running turn."""

        if self.status not in {
            TurnStatus.RECEIVED,
            TurnStatus.RUNNING,
            TurnStatus.AWAITING_CONFIRMATION,
        }:
            raise InvalidStateTransition(f"cannot fail a turn from {self.status.value}")

        return replace(
            self,
            status=TurnStatus.FAILED,
            completed_at=normalize_utc(at, field_name="at"),
        )

    def cancel(self, *, at: datetime) -> "Turn":
        """Cancel a received or running turn."""

        if self.status not in {
            TurnStatus.RECEIVED,
            TurnStatus.RUNNING,
            TurnStatus.AWAITING_CONFIRMATION,
        }:
            raise InvalidStateTransition(f"cannot cancel a turn from {self.status.value}")

        return replace(
            self,
            status=TurnStatus.CANCELLED,
            completed_at=normalize_utc(at, field_name="at"),
        )


@dataclass(frozen=True, slots=True)
class TurnAttempt:
    """One physical attempt to process a logical turn."""

    id: TurnAttemptId
    turn_id: TurnId
    attempt_number: int
    status: TurnAttemptStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        require_positive(
            self.attempt_number,
            field_name="attempt_number",
        )

        created_at = normalize_utc(
            self.created_at,
            field_name="created_at",
        )
        object.__setattr__(self, "created_at", created_at)

        started_at = self.started_at
        completed_at = self.completed_at

        if started_at is not None:
            started_at = normalize_utc(
                started_at,
                field_name="started_at",
            )
            require_not_before(
                started_at,
                minimum=created_at,
                field_name="started_at",
                minimum_field_name="created_at",
            )
            object.__setattr__(self, "started_at", started_at)

        if completed_at is not None:
            completed_at = normalize_utc(
                completed_at,
                field_name="completed_at",
            )

            if started_at is None:
                raise DomainValidationError("completed attempt requires started_at")

            require_not_before(
                completed_at,
                minimum=started_at,
                field_name="completed_at",
                minimum_field_name="started_at",
            )
            object.__setattr__(
                self,
                "completed_at",
                completed_at,
            )

        if self.status is TurnAttemptStatus.PENDING:
            if any(
                value is not None
                for value in (
                    started_at,
                    completed_at,
                    self.failure_code,
                )
            ):
                raise DomainValidationError("pending attempt must not contain execution results")

        elif self.status in {
            TurnAttemptStatus.RUNNING,
            TurnAttemptStatus.AWAITING_CONFIRMATION,
        }:
            if started_at is None or completed_at is not None:
                raise DomainValidationError("running attempt requires started_at only")

            if self.failure_code is not None:
                raise DomainValidationError("running attempt must not have failure_code")

        elif self.status is TurnAttemptStatus.SUCCEEDED:
            if started_at is None or completed_at is None:
                raise DomainValidationError("succeeded attempt requires start and completion times")

            if self.failure_code is not None:
                raise DomainValidationError("succeeded attempt must not have failure_code")

        elif self.status in {TurnAttemptStatus.FAILED, TurnAttemptStatus.CANCELLED}:
            if started_at is None or completed_at is None:
                raise DomainValidationError("failed attempt requires start and completion times")

            if self.status is TurnAttemptStatus.FAILED and self.failure_code is None:
                raise DomainValidationError("failed attempt requires failure_code")

            if self.status is TurnAttemptStatus.CANCELLED and self.failure_code is not None:
                raise DomainValidationError("cancelled attempt must not have failure_code")

            if self.failure_code is not None:
                object.__setattr__(
                    self,
                    "failure_code",
                    require_not_blank(self.failure_code, field_name="failure_code"),
                )

        if self.status in _ATTEMPT_TERMINAL_STATUSES and completed_at is None:
            raise DomainValidationError("terminal attempt requires completed_at")

    def start(self, *, at: datetime) -> "TurnAttempt":
        """Start a pending execution attempt."""

        if self.status is not TurnAttemptStatus.PENDING:
            raise InvalidStateTransition(f"cannot start an attempt from {self.status.value}")

        return replace(
            self,
            status=TurnAttemptStatus.RUNNING,
            started_at=normalize_utc(at, field_name="at"),
        )

    def succeed(self, *, at: datetime) -> "TurnAttempt":
        """Complete a running attempt successfully."""

        if self.status is not TurnAttemptStatus.RUNNING:
            raise InvalidStateTransition(f"cannot succeed an attempt from {self.status.value}")

        return replace(
            self,
            status=TurnAttemptStatus.SUCCEEDED,
            completed_at=normalize_utc(at, field_name="at"),
        )

    def await_confirmation(self) -> "TurnAttempt":
        """Pause a running attempt without completing it."""

        if self.status is not TurnAttemptStatus.RUNNING:
            raise InvalidStateTransition(f"cannot await confirmation from {self.status.value}")
        return replace(self, status=TurnAttemptStatus.AWAITING_CONFIRMATION)

    def resume(self) -> "TurnAttempt":
        """Resume a durably paused physical attempt."""

        if self.status is not TurnAttemptStatus.AWAITING_CONFIRMATION:
            raise InvalidStateTransition(f"cannot resume an attempt from {self.status.value}")
        return replace(self, status=TurnAttemptStatus.RUNNING)

    def cancel(self, *, at: datetime) -> "TurnAttempt":
        """Cancel a pending, running, or paused attempt."""

        if self.status not in {
            TurnAttemptStatus.PENDING,
            TurnAttemptStatus.RUNNING,
            TurnAttemptStatus.AWAITING_CONFIRMATION,
        }:
            raise InvalidStateTransition(f"cannot cancel attempt from {self.status.value}")
        cancelled_at = normalize_utc(at, field_name="at")
        return replace(
            self,
            status=TurnAttemptStatus.CANCELLED,
            started_at=self.started_at or cancelled_at,
            completed_at=cancelled_at,
            failure_code=None,
        )

    def fail(
        self,
        *,
        at: datetime,
        failure_code: str,
    ) -> "TurnAttempt":
        """Complete a running attempt with a classified failure."""

        if self.status is not TurnAttemptStatus.RUNNING:
            raise InvalidStateTransition(f"cannot fail an attempt from {self.status.value}")

        return replace(
            self,
            status=TurnAttemptStatus.FAILED,
            completed_at=normalize_utc(at, field_name="at"),
            failure_code=require_not_blank(
                failure_code,
                field_name="failure_code",
            ),
        )
