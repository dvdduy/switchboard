import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from switchboard.adapters.persistence.schema import execution_events
from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.application.errors import (
    TurnAttemptLifecycleConflictError,
    TurnLifecycleConflictError,
)
from switchboard.domain.execution_events import (
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    ExecutionEventId,
)
from tests.integration.support import seed_running_turn, seed_turn


async def test_event_append_advances_turn_sequence(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        event = await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=ExecutionEventId(uuid4()),
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={},
            occurred_at=now,
        )
        await unit_of_work.commit()

    assert event.sequence == 1

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_turn.next_event_sequence == 2
    assert events == (event,)


async def test_event_query_uses_exclusive_cursor(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        for text in ("one", "two", "three"):
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=ExecutionEventId(uuid4()),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.RESPONSE_DELTA,
                payload={"text": text},
                occurred_at=now,
            )

        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=1,
            limit=100,
        )

    assert [event.sequence for event in events] == [2, 3]


async def test_concurrent_event_appends_are_ordered(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )

    async def append(text: str) -> int:
        async with unit_of_work_factory() as unit_of_work:
            event = await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=ExecutionEventId(uuid4()),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.RESPONSE_DELTA,
                payload={"text": text},
                occurred_at=now,
            )
            await unit_of_work.commit()
            return event.sequence

    sequences = await asyncio.gather(
        append("first"),
        append("second"),
    )

    assert set(sequences) == {1, 2}

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_turn.next_event_sequence == 3
    assert [event.sequence for event in events] == [1, 2]


async def test_uncommitted_event_and_counter_are_rolled_back(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=ExecutionEventId(uuid4()),
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={},
            occurred_at=now,
        )
        # Intentionally do not commit.

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_turn.next_event_sequence == 1
    assert events == ()


async def test_stale_lifecycle_updates_are_rejected(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_turn(unit_of_work_factory, now=now)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.update_turn_lifecycle(
            previous=turn,
            updated=turn.start(),
        )
        await unit_of_work.turns.update_attempt_lifecycle(
            previous=attempt,
            updated=attempt.start(at=now),
        )
        await unit_of_work.commit()

    with pytest.raises(TurnLifecycleConflictError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.fail(at=now),
            )

    with pytest.raises(TurnAttemptLifecycleConflictError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.start(at=now),
            )


async def test_event_attempt_must_belong_to_same_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)

    first_turn, _ = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )
    _, second_attempt = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.turns.append_event(
                turn_id=first_turn.id,
                event_id=ExecutionEventId(uuid4()),
                attempt_id=second_attempt.id,
                kind=ExecutionEventKind.RESPONSE_DELTA,
                payload={"text": "invalid"},
                occurred_at=now,
            )
            await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(first_turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=first_turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_turn.next_event_sequence == 1
    assert events == ()


async def test_turn_has_at_most_one_started_event(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(unit_of_work_factory, now=now)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=ExecutionEventId(uuid4()),
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_STARTED,
            payload={},
            occurred_at=now,
        )
        await unit_of_work.commit()

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                insert(execution_events).values(
                    id=ExecutionEventId(uuid4()),
                    turn_id=turn.id,
                    attempt_id=attempt.id,
                    sequence=2,
                    kind=ExecutionEventKind.TURN_STARTED.value,
                    payload={},
                    occurred_at=now,
                )
            )

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_turn.next_event_sequence == 2
    assert len(events) == 1
    assert events[0].kind is ExecutionEventKind.TURN_STARTED


async def test_turn_has_at_most_one_terminal_event(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_running_turn(
        unit_of_work_factory,
        now=now,
    )

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

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                insert(execution_events).values(
                    id=ExecutionEventId(uuid4()),
                    turn_id=turn.id,
                    attempt_id=attempt.id,
                    sequence=2,
                    kind=ExecutionEventKind.TURN_FAILED.value,
                    payload={"failure_code": "unexpected_error"},
                    occurred_at=now,
                )
            )

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )

    assert persisted_turn is not None
    assert persisted_turn.next_event_sequence == 2
    assert len(events) == 1
    assert events[0].kind is (ExecutionEventKind.TURN_COMPLETED)
