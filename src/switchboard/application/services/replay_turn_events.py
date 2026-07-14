"""Framework-independent replay-then-tail delivery of committed turn events."""

from collections.abc import AsyncIterator

from switchboard.application.errors import TurnNotFoundError
from switchboard.application.ports.sleeper import AsyncSleeper
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.execution_events import ExecutionEvent
from switchboard.domain.identifiers import TurnId


class ReplayTurnEvents:
    """Replay committed events, then poll until the turn stream terminates."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        sleeper: AsyncSleeper,
        poll_interval_seconds: float = 0.1,
        batch_size: int = 100,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")

        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        self._unit_of_work_factory = unit_of_work_factory
        self._sleeper = sleeper
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size

    async def open(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
    ) -> AsyncIterator[ExecutionEvent]:
        """Validate the request and return an independent event observer."""

        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")

        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(turn_id)

        if turn is None:
            raise TurnNotFoundError(f"turn {turn_id} was not found")

        return self._iterate(
            turn_id=turn_id,
            after_sequence=after_sequence,
        )

    async def _iterate(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
    ) -> AsyncIterator[ExecutionEvent]:
        cursor = after_sequence

        while True:
            async with self._unit_of_work_factory() as unit_of_work:
                events = await unit_of_work.turns.list_events(
                    turn_id=turn_id,
                    after_sequence=cursor,
                    limit=self._batch_size,
                )

            for event in events:
                cursor = event.sequence
                yield event

                if event.is_terminal:
                    return

            if len(events) < self._batch_size:
                await self._sleeper.sleep(self._poll_interval_seconds)
