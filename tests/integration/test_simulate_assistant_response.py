import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.application.errors import (
    TurnLifecycleConflictError,
)
from switchboard.application.use_cases.simulate_assistant_response import (
    SimulateAssistantResponse,
    SimulateAssistantResponseCommand,
)
from switchboard.domain.errors import InvalidStateTransition
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    ExecutionEventId,
    MessageId,
)
from switchboard.domain.turns import (
    TurnAttemptStatus,
    TurnStatus,
)
from tests.integration.support import seed_turn


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class ExecutionEventIdGenerator:
    def new(self) -> ExecutionEventId:
        return ExecutionEventId(uuid4())


class MessageIdGenerator:
    def new(self) -> MessageId:
        return MessageId(uuid4())


class FixedMessageIdGenerator:
    def __init__(self, value: MessageId) -> None:
        self._value = value

    def new(self) -> MessageId:
        return self._value


class CancelAfterFirstDelta(SimulateAssistantResponse):
    """Cancel the executing task after one delta is durably committed."""

    _cancelled = False

    async def _append_delta(
        self,
        *,
        command: SimulateAssistantResponseCommand,
        chunk: str,
    ) -> ExecutionEvent:
        event = await super()._append_delta(
            command=command,
            chunk=chunk,
        )

        if not self._cancelled:
            self._cancelled = True
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)

        return event


async def test_simulation_persists_complete_execution(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(
        2026,
        7,
        13,
        16,
        0,
        tzinfo=UTC,
    )
    turn, attempt = await seed_turn(
        unit_of_work_factory,
        now=now,
    )

    use_case = SimulateAssistantResponse(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(now),
        event_ids=ExecutionEventIdGenerator(),
        message_ids=MessageIdGenerator(),
    )

    result = await use_case.execute(
        SimulateAssistantResponseCommand(
            turn_id=turn.id,
            attempt_id=attempt.id,
            response_text=("Project Alpha is overdue."),
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        persisted_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)

    assert persisted_turn is not None
    assert persisted_attempt is not None

    assert persisted_turn.status is TurnStatus.COMPLETED
    assert persisted_attempt.status is TurnAttemptStatus.SUCCEEDED

    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.TURN_COMPLETED,
    ]

    assert [event.sequence for event in events] == [1, 2, 3, 4, 5, 6]

    assert events[0].payload == {"attempt_number": 1}
    assert [
        event.payload for event in events if event.kind is ExecutionEventKind.RESPONSE_DELTA
    ] == [
        {"text": "Project "},
        {"text": "Alpha "},
        {"text": "is "},
        {"text": "overdue."},
    ]
    assert events[-1].payload == {
        "message_id": str(result.assistant_message_id),
        "chunk_count": 4,
    }

    delta_text = "".join(
        str(event.payload["text"])
        for event in events
        if event.kind is ExecutionEventKind.RESPONSE_DELTA
    )

    assert delta_text == "Project Alpha is overdue."

    assert len(messages) == 2
    assert messages[1].id == result.assistant_message_id
    assert messages[1].content == ("Project Alpha is overdue.")

    assert result.chunk_count == 4
    assert result.first_event_sequence == 1
    assert result.last_event_sequence == 6


async def test_completion_failure_rolls_back_success_and_records_durable_failure(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_turn(unit_of_work_factory, now=now)

    use_case = SimulateAssistantResponse(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(now),
        event_ids=ExecutionEventIdGenerator(),
        # Reusing the input-message identity makes the final message insert
        # fail after the success event was staged in the same transaction.
        message_ids=FixedMessageIdGenerator(turn.input_message_id),
    )

    with pytest.raises(IntegrityError):
        await use_case.execute(
            SimulateAssistantResponseCommand(
                turn_id=turn.id,
                attempt_id=attempt.id,
                response_text="Partial response.",
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        persisted_turn = await unit_of_work.turns.get(turn.id)
        persisted_attempt = await unit_of_work.turns.get_attempt(attempt.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)

    assert persisted_turn is not None
    assert persisted_attempt is not None
    assert persisted_turn.status is TurnStatus.FAILED
    assert persisted_attempt.status is TurnAttemptStatus.FAILED
    assert persisted_attempt.failure_code == "simulation_failed"

    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.TURN_FAILED,
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert events[-1].payload == {"failure_code": "simulation_failed"}
    assert all(event.kind is not ExecutionEventKind.TURN_COMPLETED for event in events)

    assert len(messages) == 1
    assert messages[0].id == turn.input_message_id


async def test_cancellation_after_partial_progress_records_durable_failure(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    turn, attempt = await seed_turn(unit_of_work_factory, now=now)

    use_case = CancelAfterFirstDelta(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(now),
        event_ids=ExecutionEventIdGenerator(),
        message_ids=MessageIdGenerator(),
    )

    execution_task = asyncio.create_task(
        use_case.execute(
            SimulateAssistantResponseCommand(
                turn_id=turn.id,
                attempt_id=attempt.id,
                response_text="Partial response.",
            )
        )
    )

    with pytest.raises(asyncio.CancelledError):
        await execution_task

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
    assert persisted_turn.status is TurnStatus.FAILED
    assert persisted_attempt.status is TurnAttemptStatus.FAILED
    assert persisted_attempt.failure_code == "simulation_failed"
    assert [event.kind for event in events] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.RESPONSE_DELTA,
        ExecutionEventKind.TURN_FAILED,
    ]
    assert [event.sequence for event in events] == [1, 2, 3]


async def test_only_one_competing_simulation_can_start(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(
        2026,
        7,
        13,
        16,
        0,
        tzinfo=UTC,
    )
    turn, attempt = await seed_turn(
        unit_of_work_factory,
        now=now,
    )

    def build_use_case() -> SimulateAssistantResponse:
        return SimulateAssistantResponse(
            unit_of_work_factory=unit_of_work_factory,
            clock=FixedClock(now),
            event_ids=ExecutionEventIdGenerator(),
            message_ids=MessageIdGenerator(),
        )

    results = await asyncio.gather(
        build_use_case().execute(
            SimulateAssistantResponseCommand(
                turn_id=turn.id,
                attempt_id=attempt.id,
                response_text="First response.",
            )
        ),
        build_use_case().execute(
            SimulateAssistantResponseCommand(
                turn_id=turn.id,
                attempt_id=attempt.id,
                response_text="Second response.",
            )
        ),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]

    assert len(successes) == 1
    assert len(failures) == 1

    assert isinstance(
        failures[0],
        (
            TurnLifecycleConflictError,
            InvalidStateTransition,
        ),
    )

    async with unit_of_work_factory() as unit_of_work:
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=100,
        )
        messages = await unit_of_work.conversations.list_messages(turn.conversation_id)

    assert sum(event.kind is ExecutionEventKind.TURN_STARTED for event in events) == 1

    assert sum(event.kind is ExecutionEventKind.TURN_COMPLETED for event in events) == 1

    assert len(messages) == 2
