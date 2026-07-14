import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.bootstrap.config import Settings
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import ExecutionEventId
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from tests.integration.support import seed_running_turn


class PollGateSleeper:
    def __init__(self, expected_observers: int) -> None:
        self._expected_observers = expected_observers
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def sleep(self, delay_seconds: float) -> None:
        assert delay_seconds > 0
        self.calls += 1

        if self.calls >= self._expected_observers:
            self.reached.set()

        await self.release.wait()


def make_test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": "postgresql+psycopg://unused/unused",
            "redis_url": "redis://unused/0",
        }
    )


def make_app(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    sleeper: PollGateSleeper,
):
    return create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
        replay_turn_events=ReplayTurnEvents(
            unit_of_work_factory=unit_of_work_factory,
            sleeper=sleeper,
        ),
    )


async def team_headers(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    conversation_id,
) -> dict[str, str]:
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(conversation_id)
    assert conversation is not None
    return {"X-Team-ID": str(conversation.team_id)}


async def append_started_event(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    turn_id,
    attempt_id,
    now: datetime,
) -> None:
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.append_event(
            turn_id=turn_id,
            event_id=ExecutionEventId(uuid4()),
            attempt_id=attempt_id,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={"attempt_number": 1},
            occurred_at=now,
        )
        await unit_of_work.commit()


async def complete_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    turn_id,
    attempt_id,
    now: datetime,
) -> None:
    async with unit_of_work_factory() as unit_of_work:
        turn = await unit_of_work.turns.get(turn_id)
        attempt = await unit_of_work.turns.get_attempt(attempt_id)
        assert turn is not None
        assert attempt is not None

        await unit_of_work.turns.update_turn_lifecycle(
            previous=turn,
            updated=turn.complete(at=now),
        )
        await unit_of_work.turns.update_attempt_lifecycle(
            previous=attempt,
            updated=attempt.succeed(at=now),
        )
        await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=ExecutionEventId(uuid4()),
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_COMPLETED,
            payload={},
            occurred_at=now,
        )
        await unit_of_work.commit()


async def test_terminal_stream_replays_remaining_events_and_closes(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(unit_of_work_factory, now=now)
    headers = await team_headers(unit_of_work_factory, turn.conversation_id)
    await append_started_event(
        unit_of_work_factory,
        turn_id=turn.id,
        attempt_id=attempt.id,
        now=now,
    )
    await complete_turn(
        unit_of_work_factory,
        turn_id=turn.id,
        attempt_id=attempt.id,
        now=now,
    )

    sleeper = PollGateSleeper(expected_observers=1)
    transport = ASGITransport(app=make_app(unit_of_work_factory, sleeper))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/turns/{turn.id}/events",
            headers={**headers, "Last-Event-ID": "1"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert response.text == "id: 2\nevent: turn.completed\ndata: {}\n\n"
    assert sleeper.calls == 0


async def test_cross_team_stream_is_rejected_before_streaming(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, _ = await seed_running_turn(unit_of_work_factory, now=now)
    sleeper = PollGateSleeper(expected_observers=1)
    transport = ASGITransport(app=make_app(unit_of_work_factory, sleeper))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/turns/{turn.id}/events",
            headers={"X-Team-ID": str(uuid4())},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "The requested resource was not found.",
        }
    }
    assert "text/event-stream" not in response.headers["content-type"]
    assert sleeper.calls == 0


async def test_running_stream_tails_for_two_independent_observers(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(unit_of_work_factory, now=now)
    headers = await team_headers(unit_of_work_factory, turn.conversation_id)
    await append_started_event(
        unit_of_work_factory,
        turn_id=turn.id,
        attempt_id=attempt.id,
        now=now,
    )

    sleeper = PollGateSleeper(expected_observers=2)
    transport = ASGITransport(app=make_app(unit_of_work_factory, sleeper))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_request = asyncio.create_task(
            client.get(f"/api/v1/turns/{turn.id}/events", headers=headers)
        )
        second_request = asyncio.create_task(
            client.get(f"/api/v1/turns/{turn.id}/events", headers=headers)
        )

        await asyncio.wait_for(sleeper.reached.wait(), timeout=1)
        await complete_turn(
            unit_of_work_factory,
            turn_id=turn.id,
            attempt_id=attempt.id,
            now=now,
        )
        sleeper.release.set()
        first_response, second_response = await asyncio.gather(
            first_request,
            second_request,
        )

    expected = (
        'id: 1\nevent: turn.started\ndata: {"attempt_number":1}\n\n'
        "id: 2\nevent: turn.completed\ndata: {}\n\n"
    )
    assert first_response.text == expected
    assert second_response.text == expected


async def test_disconnecting_observer_does_not_mutate_running_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(unit_of_work_factory, now=now)
    headers = await team_headers(unit_of_work_factory, turn.conversation_id)
    await append_started_event(
        unit_of_work_factory,
        turn_id=turn.id,
        attempt_id=attempt.id,
        now=now,
    )

    sleeper = PollGateSleeper(expected_observers=1)
    transport = ASGITransport(app=make_app(unit_of_work_factory, sleeper))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(
            client.get(f"/api/v1/turns/{turn.id}/events", headers=headers)
        )
        await asyncio.wait_for(sleeper.reached.wait(), timeout=1)
        request.cancel()

        with pytest.raises(asyncio.CancelledError):
            await request

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        persisted_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_attempt is not None
    assert persisted_turn.status is TurnStatus.RUNNING
    assert persisted_attempt.status is TurnAttemptStatus.RUNNING
    assert [event.kind for event in events] == [ExecutionEventKind.TURN_STARTED]
