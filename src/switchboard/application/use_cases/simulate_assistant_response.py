"""Application workflow for deterministic durable assistant execution."""

import asyncio
import logging
from dataclasses import dataclass

from switchboard.application.errors import (
    TurnAttemptMismatchError,
    TurnAttemptNotFoundError,
    TurnNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import (
    IdGenerator,
)
from switchboard.application.ports.unit_of_work import (
    UnitOfWork,
    UnitOfWorkFactory,
)
from switchboard.application.services.response_chunking import (
    chunk_response_text,
)
from switchboard.domain.common import require_not_blank
from switchboard.domain.conversations import MessageRole
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    ExecutionEventId,
    MessageId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import (
    Turn,
    TurnAttempt,
    TurnAttemptStatus,
    TurnStatus,
)

logger = logging.getLogger(__name__)

_FAILURE_CODE = "simulation_failed"


@dataclass(frozen=True, slots=True)
class SimulateAssistantResponseCommand:
    """Input for one deterministic physical turn attempt."""

    turn_id: TurnId
    attempt_id: TurnAttemptId
    response_text: str


@dataclass(frozen=True, slots=True)
class SimulateAssistantResponseResult:
    """Durable identities and event range created by execution."""

    turn_id: TurnId
    attempt_id: TurnAttemptId
    assistant_message_id: MessageId
    chunk_count: int
    first_event_sequence: int
    last_event_sequence: int


class SimulateAssistantResponse:
    """Execute one turn attempt using deterministic response chunks."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        event_ids: IdGenerator[ExecutionEventId],
        message_ids: IdGenerator[MessageId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._event_ids = event_ids
        self._message_ids = message_ids

    async def execute(
        self,
        command: SimulateAssistantResponseCommand,
    ) -> SimulateAssistantResponseResult:
        """Persist a complete simulated execution."""

        response_text = require_not_blank(
            command.response_text,
            field_name="response_text",
        )
        chunks = chunk_response_text(response_text)
        execution_started = False

        try:
            started_event = await self._start_execution(command)
            execution_started = True

            for chunk in chunks:
                await self._append_delta(
                    command=command,
                    chunk=chunk,
                )

            assistant_message_id = self._message_ids.new()

            completed_event = await self._complete_execution(
                command=command,
                assistant_message_id=(assistant_message_id),
                response_text=response_text,
                chunk_count=len(chunks),
            )

            return SimulateAssistantResponseResult(
                turn_id=command.turn_id,
                attempt_id=command.attempt_id,
                assistant_message_id=(assistant_message_id),
                chunk_count=len(chunks),
                first_event_sequence=(started_event.sequence),
                last_event_sequence=(completed_event.sequence),
            )

        except asyncio.CancelledError:
            if execution_started:
                failure_task = asyncio.create_task(self._record_failure_safely(command))
                await asyncio.shield(failure_task)
            raise

        except Exception:
            if execution_started:
                await self._record_failure_safely(command)
            raise

    async def _start_execution(
        self,
        command: SimulateAssistantResponseCommand,
    ) -> ExecutionEvent:
        started_at = self._clock.now()

        async with self._unit_of_work_factory() as unit_of_work:
            turn, attempt = await self._load_execution(
                unit_of_work,
                command,
            )

            running_turn = turn.start()
            running_attempt = attempt.start(at=started_at)

            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=running_turn,
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=running_attempt,
            )

            event = await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_STARTED,
                payload={
                    "attempt_number": (attempt.attempt_number),
                },
                occurred_at=started_at,
            )

            await unit_of_work.commit()

        return event

    async def _append_delta(
        self,
        *,
        command: SimulateAssistantResponseCommand,
        chunk: str,
    ) -> ExecutionEvent:
        occurred_at = self._clock.now()

        async with self._unit_of_work_factory() as unit_of_work:
            turn, attempt = await self._load_execution(
                unit_of_work,
                command,
            )

            if turn.status is not TurnStatus.RUNNING:
                raise RuntimeError("response delta requires a running turn")

            if attempt.status is not TurnAttemptStatus.RUNNING:
                raise RuntimeError("response delta requires a running attempt")

            event = await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.RESPONSE_DELTA,
                payload={"text": chunk},
                occurred_at=occurred_at,
            )

            await unit_of_work.commit()

        return event

    async def _complete_execution(
        self,
        *,
        command: SimulateAssistantResponseCommand,
        assistant_message_id: MessageId,
        response_text: str,
        chunk_count: int,
    ) -> ExecutionEvent:
        completed_at = self._clock.now()

        async with self._unit_of_work_factory() as unit_of_work:
            turn, attempt = await self._load_execution(
                unit_of_work,
                command,
            )

            completed_turn = turn.complete(at=completed_at)
            succeeded_attempt = attempt.succeed(at=completed_at)

            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=completed_turn,
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=succeeded_attempt,
            )

            terminal_event = await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=(ExecutionEventKind.TURN_COMPLETED),
                payload={
                    "message_id": str(assistant_message_id),
                    "chunk_count": chunk_count,
                },
                occurred_at=completed_at,
            )

            await unit_of_work.conversations.append_message(
                conversation_id=turn.conversation_id,
                message_id=assistant_message_id,
                role=MessageRole.ASSISTANT,
                content=response_text,
                created_at=completed_at,
            )

            await unit_of_work.commit()

        return terminal_event

    async def _load_execution(
        self,
        unit_of_work: UnitOfWork,
        command: SimulateAssistantResponseCommand,
    ) -> tuple[Turn, TurnAttempt]:
        turn = await unit_of_work.turns.get(command.turn_id)

        if turn is None:
            raise TurnNotFoundError(f"turn {command.turn_id} was not found")

        attempt = await unit_of_work.turns.get_attempt(command.attempt_id)

        if attempt is None:
            raise TurnAttemptNotFoundError(f"turn attempt {command.attempt_id} was not found")

        if attempt.turn_id != turn.id:
            raise TurnAttemptMismatchError(
                f"turn attempt {attempt.id} does not belong to turn {turn.id}"
            )

        return turn, attempt

    async def _record_failure_safely(
        self,
        command: SimulateAssistantResponseCommand,
    ) -> None:
        try:
            await self._record_failure(command)
        except Exception:
            logger.exception(
                "failed to record terminal simulation failure for turn %s attempt %s",
                command.turn_id,
                command.attempt_id,
            )

    async def _record_failure(
        self,
        command: SimulateAssistantResponseCommand,
    ) -> None:
        failed_at = self._clock.now()

        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(command.turn_id)
            attempt = await unit_of_work.turns.get_attempt(command.attempt_id)

            # The original failure may have happened before
            # execution started, or another worker may already
            # have completed the attempt.
            if turn is None or attempt is None:
                return

            if attempt.turn_id != turn.id:
                return

            if turn.status is not TurnStatus.RUNNING:
                return

            if attempt.status is not TurnAttemptStatus.RUNNING:
                return

            failed_turn = turn.fail(at=failed_at)
            failed_attempt = attempt.fail(
                at=failed_at,
                failure_code=_FAILURE_CODE,
            )

            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=failed_turn,
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=failed_attempt,
            )

            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_FAILED,
                payload={
                    "failure_code": _FAILURE_CODE,
                },
                occurred_at=failed_at,
            )

            await unit_of_work.commit()
