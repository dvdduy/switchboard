from datetime import UTC, datetime
from uuid import uuid4

from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import ExecutionEventId
from tests.integration.support import seed_running_turn


class NeverSleeper:
    async def sleep(self, delay_seconds: float) -> None:
        raise AssertionError(f"terminal replay unexpectedly slept for {delay_seconds}")


async def test_replays_postgres_events_after_exclusive_cursor(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(unit_of_work_factory, now=now)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=ExecutionEventId(uuid4()),
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={"attempt_number": 1},
            occurred_at=now,
        )
        await unit_of_work.commit()

    completed_turn = turn.complete(at=now)
    succeeded_attempt = attempt.succeed(at=now)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.update_turn_lifecycle(
            previous=turn,
            updated=completed_turn,
        )
        await unit_of_work.turns.update_attempt_lifecycle(
            previous=attempt,
            updated=succeeded_attempt,
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

    service = ReplayTurnEvents(
        unit_of_work_factory=unit_of_work_factory,
        sleeper=NeverSleeper(),
        batch_size=1,
    )
    observer = await service.open(turn_id=turn.id, after_sequence=1)
    events = [event async for event in observer]

    assert [event.sequence for event in events] == [2]
    assert events[0].kind is ExecutionEventKind.TURN_COMPLETED
