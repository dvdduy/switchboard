from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.turn_events import serialize_sse_event
from switchboard.application.errors import TurnNotFoundError
from switchboard.application.services.readiness import ReadinessService
from switchboard.bootstrap.config import Settings
from switchboard.domain.execution_events import ExecutionEvent, ExecutionEventKind
from switchboard.domain.identifiers import ExecutionEventId, TurnId


class FakeReplayTurnEvents:
    def __init__(
        self,
        *,
        events: tuple[ExecutionEvent, ...] = (),
        error: TurnNotFoundError | None = None,
    ) -> None:
        self._events = events
        self._error = error
        self.opened: list[tuple[TurnId, int]] = []

    async def open(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
    ) -> AsyncIterator[ExecutionEvent]:
        self.opened.append((turn_id, after_sequence))

        if self._error is not None:
            raise self._error

        async def iterate() -> AsyncIterator[ExecutionEvent]:
            for event in self._events:
                yield event

        return iterate()


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": "postgresql+psycopg://user:password@localhost/test",
            "redis_url": "redis://localhost:6379/15",
        }
    )


def make_event(
    *,
    turn_id: TurnId | None = None,
    sequence: int = 1,
    kind: ExecutionEventKind = ExecutionEventKind.TURN_COMPLETED,
) -> ExecutionEvent:
    return ExecutionEvent(
        id=ExecutionEventId(uuid4()),
        turn_id=TurnId(uuid4()) if turn_id is None else turn_id,
        attempt_id=None,
        sequence=sequence,
        kind=kind,
        payload={
            "text": "héllo",
            "metadata": {"indexes": [1, 2]},
        },
        occurred_at=datetime(2026, 7, 13, tzinfo=UTC),
    )


def make_app(replay_turn_events: FakeReplayTurnEvents):
    return create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
        replay_turn_events=replay_turn_events,
    )


def test_serializes_exact_compact_sse_frame() -> None:
    event = make_event(sequence=7, kind=ExecutionEventKind.RESPONSE_DELTA)

    assert serialize_sse_event(event) == (
        'id: 7\nevent: response.delta\ndata: {"text":"héllo","metadata":{"indexes":[1,2]}}\n\n'
    )


async def test_missing_last_event_id_uses_zero_cursor() -> None:
    turn_id = TurnId(uuid4())
    event = make_event(turn_id=turn_id)
    replay = FakeReplayTurnEvents(events=(event,))
    transport = ASGITransport(app=make_app(replay))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/turns/{turn_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert replay.opened == [(turn_id, 0)]


@pytest.mark.parametrize(
    "last_event_id",
    ["not-an-integer", "-1", "1.0", "+1", " 1"],
)
async def test_invalid_last_event_id_fails_before_streaming(
    last_event_id: str,
) -> None:
    turn_id = TurnId(uuid4())
    replay = FakeReplayTurnEvents(events=(make_event(turn_id=turn_id),))
    transport = ASGITransport(app=make_app(replay))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/turns/{turn_id}/events",
            headers={"Last-Event-ID": last_event_id},
        )

    assert response.status_code == 422
    assert replay.opened == []


async def test_missing_turn_returns_404_before_streaming() -> None:
    turn_id = TurnId(uuid4())
    replay = FakeReplayTurnEvents(error=TurnNotFoundError(f"turn {turn_id} was not found"))
    transport = ASGITransport(app=make_app(replay))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/turns/{turn_id}/events")

    assert response.status_code == 404
    assert response.json() == {"detail": f"turn {turn_id} was not found"}
    assert "text/event-stream" not in response.headers["content-type"]
