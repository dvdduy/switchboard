from collections.abc import Mapping
from datetime import UTC, datetime
from operator import setitem
from typing import cast
from uuid import uuid4

import pytest

from switchboard.domain.errors import DomainValidationError
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    ExecutionEventId,
    TurnAttemptId,
    TurnId,
)


def make_event(
    *,
    kind: ExecutionEventKind = (ExecutionEventKind.RESPONSE_DELTA),
    payload: Mapping[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        id=ExecutionEventId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        sequence=1,
        kind=kind,
        payload=({"text": "Project "} if payload is None else payload),
        occurred_at=datetime(
            2026,
            7,
            13,
            16,
            0,
            tzinfo=UTC,
        ),
    )


def test_execution_event_freezes_nested_payload() -> None:
    event = make_event(
        payload={
            "text": "Project ",
            "metadata": {
                "indexes": [1, 2],
            },
        }
    )

    with pytest.raises(TypeError):
        setitem(
            event.payload,
            "text",
            "Changed",
        )

    metadata = event.payload["metadata"]

    assert isinstance(metadata, Mapping)

    with pytest.raises(TypeError):
        setitem(
            metadata,
            "other",
            True,
        )

    assert metadata["indexes"] == (1, 2)


def test_execution_event_defensively_copies_payload() -> None:
    indexes = [1, 2]
    metadata: dict[str, object] = {"indexes": indexes}
    payload: dict[str, object] = {"metadata": metadata}

    event = make_event(payload=payload)

    indexes.append(3)
    metadata["visible"] = False
    payload["text"] = "Changed"

    assert event.payload == {
        "metadata": {
            "indexes": (1, 2),
        }
    }


def test_execution_event_sequence_must_be_positive() -> None:
    with pytest.raises(
        DomainValidationError,
        match="sequence must be greater than zero",
    ):
        ExecutionEvent(
            id=ExecutionEventId(uuid4()),
            turn_id=TurnId(uuid4()),
            attempt_id=None,
            sequence=0,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={},
            occurred_at=datetime(
                2026,
                7,
                13,
                tzinfo=UTC,
            ),
        )


def test_execution_event_rejects_non_string_object_keys() -> None:
    invalid_payload = cast(
        Mapping[str, object],
        {1: "invalid"},
    )

    with pytest.raises(
        DomainValidationError,
        match="object keys must be strings",
    ):
        make_event(payload=invalid_payload)


@pytest.mark.parametrize(
    "number",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_execution_event_rejects_non_finite_numbers(
    number: float,
) -> None:
    with pytest.raises(
        DomainValidationError,
        match="must contain only finite numbers",
    ):
        make_event(payload={"score": number})


def test_execution_event_rejects_unsupported_values() -> None:
    with pytest.raises(
        DomainValidationError,
        match="contains unsupported JSON value set",
    ):
        make_event(payload={"tags": {"unsafe"}})


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ExecutionEventKind.TURN_STARTED, False),
        (ExecutionEventKind.RESPONSE_DELTA, False),
        (ExecutionEventKind.APPROVAL_REQUIRED, False),
        (ExecutionEventKind.APPROVAL_RESOLVED, False),
        (ExecutionEventKind.TURN_COMPLETED, True),
        (ExecutionEventKind.TURN_FAILED, True),
        (ExecutionEventKind.TURN_CANCELLED, True),
    ],
)
def test_terminal_event_classification(
    kind: ExecutionEventKind,
    expected: bool,
) -> None:
    event = make_event(
        kind=kind,
        payload={},
    )

    assert event.is_terminal is expected
