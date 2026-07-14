"""Immutable structured events emitted during durable turn execution."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import (
    normalize_utc,
    require_positive,
)
from switchboard.domain.identifiers import (
    ExecutionEventId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.json_values import JsonObject, freeze_json_object


class ExecutionEventKind(StrEnum):
    """Stable public event names exposed by Switchboard."""

    TURN_STARTED = "turn.started"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_RESOLVED = "approval.resolved"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    RESPONSE_DELTA = "response.delta"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_CANCELLED = "turn.cancelled"


_TERMINAL_EVENT_KINDS = frozenset(
    {
        ExecutionEventKind.TURN_COMPLETED,
        ExecutionEventKind.TURN_FAILED,
        ExecutionEventKind.TURN_CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """One immutable durable observation from turn execution."""

    id: ExecutionEventId
    turn_id: TurnId
    attempt_id: TurnAttemptId | None
    sequence: int
    kind: ExecutionEventKind
    payload: JsonObject
    occurred_at: datetime

    def __post_init__(self) -> None:
        require_positive(
            self.sequence,
            field_name="sequence",
        )

        object.__setattr__(
            self,
            "payload",
            freeze_json_object(self.payload, field_name="payload"),
        )

        object.__setattr__(
            self,
            "occurred_at",
            normalize_utc(
                self.occurred_at,
                field_name="occurred_at",
            ),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this event closes the public turn stream."""

        return self.kind in _TERMINAL_EVENT_KINDS
