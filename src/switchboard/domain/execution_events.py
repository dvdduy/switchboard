"""Immutable structured events emitted during durable turn execution."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import cast

from switchboard.domain.common import (
    normalize_utc,
    require_positive,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    ExecutionEventId,
    TurnAttemptId,
    TurnId,
)

JsonObject = Mapping[str, object]


class ExecutionEventKind(StrEnum):
    """Stable public event names exposed by Switchboard."""

    TURN_STARTED = "turn.started"
    RESPONSE_DELTA = "response.delta"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"


_TERMINAL_EVENT_KINDS = frozenset(
    {
        ExecutionEventKind.TURN_COMPLETED,
        ExecutionEventKind.TURN_FAILED,
    }
)


def _freeze_json_value(
    value: object,
    *,
    path: str,
) -> object:
    """Validate and recursively freeze one JSON-compatible value."""

    if value is None or isinstance(
        value,
        (str, bool, int),
    ):
        return value

    if isinstance(value, float):
        if not isfinite(value):
            raise DomainValidationError(f"{path} must contain only finite numbers")

        return value

    if isinstance(value, Mapping):
        frozen_mapping: dict[str, object] = {}

        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise DomainValidationError(f"{path} object keys must be strings")

            nested_path = f"{path}.{key}" if path else key

            frozen_mapping[key] = _freeze_json_value(
                nested_value,
                path=nested_path,
            )

        return MappingProxyType(frozen_mapping)

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(
            _freeze_json_value(
                item,
                path=f"{path}[{index}]",
            )
            for index, item in enumerate(value)
        )

    raise DomainValidationError(f"{path} contains unsupported JSON value {type(value).__name__}")


def freeze_json_object(
    payload: Mapping[str, object],
) -> JsonObject:
    """Validate and recursively freeze a JSON object."""

    frozen = _freeze_json_value(
        payload,
        path="payload",
    )

    return cast(JsonObject, frozen)


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
            freeze_json_object(self.payload),
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
