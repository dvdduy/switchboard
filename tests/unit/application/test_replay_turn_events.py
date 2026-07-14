import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from switchboard.application.errors import TurnNotFoundError, TurnTeamMismatchError
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.domain.conversations import Conversation, ConversationStatus
from switchboard.domain.execution_events import ExecutionEvent, ExecutionEventKind
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    ExecutionEventId,
    MessageId,
    TeamId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnStatus

TEAM_ID = TeamId(uuid4())


class FakeConversationRepository:
    def __init__(self, turn: Turn | None) -> None:
        self.conversation = (
            None
            if turn is None
            else Conversation(
                id=turn.conversation_id,
                team_id=TEAM_ID,
                default_agent_version_id=turn.agent_version_id,
                status=ConversationStatus.ACTIVE,
                next_message_sequence=2,
                created_at=turn.created_at,
                updated_at=turn.created_at,
            )
        )

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        if self.conversation is None or self.conversation.id != conversation_id:
            return None
        return self.conversation


class FakeTurnRepository:
    def __init__(
        self,
        turn: Turn | None,
        events: tuple[ExecutionEvent, ...] = (),
    ) -> None:
        self.turn = turn
        self.events = list(events)
        self.queries: list[tuple[int, int]] = []

    async def get(self, turn_id: TurnId) -> Turn | None:
        if self.turn is None or self.turn.id != turn_id:
            return None

        return self.turn

    async def list_events(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
        limit: int,
    ) -> tuple[ExecutionEvent, ...]:
        assert self.turn is not None
        assert turn_id == self.turn.id
        self.queries.append((after_sequence, limit))

        return tuple(
            sorted(
                (
                    event
                    for event in self.events
                    if event.turn_id == turn_id and event.sequence > after_sequence
                ),
                key=lambda event: event.sequence,
            )[:limit]
        )


class FakeUnitOfWork:
    def __init__(
        self,
        factory: "FakeUnitOfWorkFactory",
        turns: FakeTurnRepository,
    ) -> None:
        self._factory = factory
        self.turns = turns
        self.conversations = factory.conversations

    async def __aenter__(self) -> Self:
        self._factory.active += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._factory.active -= 1


class FakeUnitOfWorkFactory:
    def __init__(self, turns: FakeTurnRepository) -> None:
        self.turns = turns
        self.conversations = FakeConversationRepository(turns.turn)
        self.created = 0
        self.active = 0

    def __call__(self) -> FakeUnitOfWork:
        self.created += 1
        return FakeUnitOfWork(self, self.turns)


class AppendingSleeper:
    def __init__(
        self,
        *,
        factory: FakeUnitOfWorkFactory,
        events: tuple[ExecutionEvent, ...],
    ) -> None:
        self._factory = factory
        self._events = events
        self.calls: list[float] = []

    async def sleep(self, delay_seconds: float) -> None:
        assert self._factory.active == 0
        self.calls.append(delay_seconds)
        self._factory.turns.events.extend(self._events)
        self._events = ()


class BlockingSleeper:
    def __init__(self, factory: FakeUnitOfWorkFactory) -> None:
        self._factory = factory
        self.release = asyncio.Event()
        self.calls = 0

    async def sleep(self, delay_seconds: float) -> None:
        assert delay_seconds > 0
        assert self._factory.active == 0
        self.calls += 1
        await self.release.wait()


def make_turn() -> Turn:
    return Turn(
        id=TurnId(uuid4()),
        conversation_id=ConversationId(uuid4()),
        input_message_id=MessageId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        status=TurnStatus.RUNNING,
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )


def make_event(
    turn: Turn,
    sequence: int,
    kind: ExecutionEventKind,
) -> ExecutionEvent:
    return ExecutionEvent(
        id=ExecutionEventId(uuid4()),
        turn_id=turn.id,
        attempt_id=None,
        sequence=sequence,
        kind=kind,
        payload={},
        occurred_at=turn.created_at,
    )


async def collect_events(
    observer: AsyncIterator[ExecutionEvent],
) -> list[ExecutionEvent]:
    return [event async for event in observer]


async def test_negative_cursor_is_rejected_before_preflight() -> None:
    turn = make_turn()
    repository = FakeTurnRepository(turn)
    factory = FakeUnitOfWorkFactory(repository)
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=AppendingSleeper(factory=factory, events=()),
    )

    with pytest.raises(ValueError, match="after_sequence must not be negative"):
        await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=-1)

    assert factory.created == 0


async def test_missing_turn_is_reported_during_preflight() -> None:
    repository = FakeTurnRepository(None)
    factory = FakeUnitOfWorkFactory(repository)
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=AppendingSleeper(factory=factory, events=()),
    )

    with pytest.raises(TurnNotFoundError, match="was not found"):
        await service.open(team_id=TEAM_ID, turn_id=TurnId(uuid4()), after_sequence=0)

    assert factory.created == 1
    assert factory.active == 0


async def test_cross_team_turn_is_rejected_during_preflight() -> None:
    turn = make_turn()
    repository = FakeTurnRepository(turn)
    factory = FakeUnitOfWorkFactory(repository)
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=AppendingSleeper(factory=factory, events=()),
    )

    with pytest.raises(TurnTeamMismatchError):
        await service.open(
            team_id=TeamId(uuid4()),
            turn_id=turn.id,
            after_sequence=0,
        )

    assert factory.created == 1
    assert factory.active == 0


async def test_replays_after_exclusive_cursor_without_open_transaction() -> None:
    turn = make_turn()
    events = (
        make_event(turn, 1, ExecutionEventKind.TURN_STARTED),
        make_event(turn, 2, ExecutionEventKind.RESPONSE_DELTA),
        make_event(turn, 3, ExecutionEventKind.TURN_COMPLETED),
    )
    repository = FakeTurnRepository(turn, events)
    factory = FakeUnitOfWorkFactory(repository)
    sleeper = AppendingSleeper(factory=factory, events=())
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=sleeper,
        batch_size=1,
    )

    observer = await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=1)
    first = await anext(observer)

    assert first.sequence == 2
    assert factory.active == 0

    remaining = [event async for event in observer]

    assert [event.sequence for event in remaining] == [3]
    assert repository.queries == [(1, 1), (2, 1)]
    assert sleeper.calls == []
    assert factory.active == 0


async def test_tails_new_events_after_sleeping_when_caught_up() -> None:
    turn = make_turn()
    started = make_event(turn, 1, ExecutionEventKind.TURN_STARTED)
    delta = make_event(turn, 2, ExecutionEventKind.RESPONSE_DELTA)
    terminal = make_event(turn, 3, ExecutionEventKind.TURN_COMPLETED)
    repository = FakeTurnRepository(turn, (started,))
    factory = FakeUnitOfWorkFactory(repository)
    sleeper = AppendingSleeper(
        factory=factory,
        events=(delta, terminal),
    )
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=sleeper,
        poll_interval_seconds=0.25,
    )

    observer = await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=0)
    events = [event async for event in observer]

    assert [event.sequence for event in events] == [1, 2, 3]
    assert sleeper.calls == [0.25]
    assert repository.queries == [(0, 100), (1, 100)]
    assert factory.active == 0


async def test_multiple_observers_keep_independent_cursors() -> None:
    turn = make_turn()
    events = (
        make_event(turn, 1, ExecutionEventKind.TURN_STARTED),
        make_event(turn, 2, ExecutionEventKind.TURN_COMPLETED),
    )
    repository = FakeTurnRepository(turn, events)
    factory = FakeUnitOfWorkFactory(repository)
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=AppendingSleeper(factory=factory, events=()),
    )

    first = await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=0)
    second = await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=0)
    first_events, second_events = await asyncio.gather(
        collect_events(first),
        collect_events(second),
    )

    assert [event.sequence for event in first_events] == [1, 2]
    assert [event.sequence for event in second_events] == [1, 2]
    assert repository.queries == [(0, 100), (0, 100)]


async def test_cancelling_one_observer_does_not_stop_another() -> None:
    turn = make_turn()
    started = make_event(turn, 1, ExecutionEventKind.TURN_STARTED)
    terminal = make_event(turn, 2, ExecutionEventKind.TURN_FAILED)
    repository = FakeTurnRepository(turn, (started,))
    factory = FakeUnitOfWorkFactory(repository)
    sleeper = BlockingSleeper(factory)
    service = ReplayTurnEvents(
        unit_of_work_factory=factory,
        sleeper=sleeper,
    )

    cancelled_observer = await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=0)
    surviving_observer = await service.open(team_id=TEAM_ID, turn_id=turn.id, after_sequence=0)
    assert (await anext(cancelled_observer)).sequence == 1
    assert (await anext(surviving_observer)).sequence == 1

    cancelled_poll = asyncio.create_task(anext(cancelled_observer))
    surviving_poll = asyncio.create_task(anext(surviving_observer))

    while sleeper.calls < 2:
        await asyncio.sleep(0)

    cancelled_poll.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cancelled_poll

    repository.events.append(terminal)
    sleeper.release.set()

    assert (await surviving_poll).sequence == 2
    with pytest.raises(StopAsyncIteration):
        await anext(surviving_observer)

    assert repository.queries == [(0, 100), (0, 100), (1, 100)]
