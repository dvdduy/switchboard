"""Durable execution workflow for one bounded orchestrated agent turn."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from switchboard.application.errors import (
    ConversationNotFoundError,
    TurnAttemptMismatchError,
    TurnAttemptNotFoundError,
    TurnNotFoundError,
    TurnTeamMismatchError,
)
from switchboard.application.ports.agent_orchestrator import (
    AgentOrchestrator,
    OrchestrationRequest,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.model_gateway import (
    MAX_MODEL_TOOL_CANDIDATES,
    ModelContextItem,
    ModelRequest,
    ModelRequestPhase,
    ModelToolDescriptor,
)
from switchboard.application.ports.tool_adapter import ToolAdapterResolver
from switchboard.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from switchboard.application.services.response_chunking import chunk_response_text
from switchboard.application.services.tool_dispatch import (
    DurableToolCallHandler,
    ToolDispatchContext,
)
from switchboard.application.use_cases.build_turn_context import (
    BuildTurnContextCommand,
)
from switchboard.domain.context import BuiltContext
from switchboard.domain.conversations import MessageRole
from switchboard.domain.execution_events import ExecutionEvent, ExecutionEventKind
from switchboard.domain.identifiers import (
    ExecutionEventId,
    MessageId,
    TeamId,
    ToolInvocationId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.tools import EligibleTool, ToolEffect
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus

logger = logging.getLogger(__name__)

_FAILURE_CODE = "agent_execution_failed"


class TurnContextBuilder(Protocol):
    """Build the reproducible bounded context for one pinned turn."""

    async def execute(self, command: BuildTurnContextCommand) -> BuiltContext:
        """Return one bounded context snapshot."""


@dataclass(frozen=True, slots=True)
class RunTurnCommand:
    """Trusted development execution authority for one physical attempt."""

    team_id: TeamId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    granted_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "granted_scopes", tuple(sorted(set(self.granted_scopes))))


@dataclass(frozen=True, slots=True)
class RunTurnResult:
    """Durable identities and event range produced by one completed run."""

    turn_id: TurnId
    attempt_id: TurnAttemptId
    assistant_message_id: MessageId
    tool_called: bool
    chunk_count: int
    first_event_sequence: int
    last_event_sequence: int


class RunTurn:
    """Run one bounded agent turn while PostgreSQL owns durable truth."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        context_builder: TurnContextBuilder,
        orchestrator: AgentOrchestrator,
        adapter_resolver: ToolAdapterResolver,
        schema_validator: JsonSchemaValidator,
        clock: Clock,
        invocation_ids: IdGenerator[ToolInvocationId],
        event_ids: IdGenerator[ExecutionEventId],
        message_ids: IdGenerator[MessageId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._context_builder = context_builder
        self._orchestrator = orchestrator
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._clock = clock
        self._invocation_ids = invocation_ids
        self._event_ids = event_ids
        self._message_ids = message_ids

    async def execute(self, command: RunTurnCommand) -> RunTurnResult:
        """Execute explicitly; no HTTP request or process-local task starts this work."""

        execution_started = False
        try:
            started_event = await self._start_execution(command)
            execution_started = True

            built_context = await self._context_builder.execute(
                BuildTurnContextCommand(command.turn_id)
            )
            eligible_tools = await self._load_eligible_tools(command, built_context)
            initial_request = self._model_request(built_context, eligible_tools)
            tool_handler = DurableToolCallHandler(
                context=ToolDispatchContext(
                    team_id=command.team_id,
                    agent_version_id=built_context.agent_version_id,
                    turn_id=command.turn_id,
                    attempt_id=command.attempt_id,
                    granted_scopes=command.granted_scopes,
                ),
                unit_of_work_factory=self._unit_of_work_factory,
                adapter_resolver=self._adapter_resolver,
                schema_validator=self._schema_validator,
                clock=self._clock,
                invocation_ids=self._invocation_ids,
                event_ids=self._event_ids,
            )
            orchestration = await self._orchestrator.run(
                OrchestrationRequest(initial_request),
                tool_handler=tool_handler,
            )
            chunks = chunk_response_text(orchestration.response_text)
            for chunk in chunks:
                await self._append_delta(command=command, chunk=chunk)

            assistant_message_id = self._message_ids.new()
            completed_event = await self._complete_execution(
                command=command,
                assistant_message_id=assistant_message_id,
                response_text=orchestration.response_text,
                chunk_count=len(chunks),
            )
            return RunTurnResult(
                turn_id=command.turn_id,
                attempt_id=command.attempt_id,
                assistant_message_id=assistant_message_id,
                tool_called=orchestration.tool_called,
                chunk_count=len(chunks),
                first_event_sequence=started_event.sequence,
                last_event_sequence=completed_event.sequence,
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

    async def _start_execution(self, command: RunTurnCommand) -> ExecutionEvent:
        started_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn, attempt = await self._load_execution(unit_of_work, command)
            conversation = await unit_of_work.conversations.get(turn.conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(
                    f"conversation {turn.conversation_id} was not found"
                )
            if conversation.team_id != command.team_id:
                raise TurnTeamMismatchError(f"turn {turn.id} was not found")
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
                payload={"attempt_number": attempt.attempt_number},
                occurred_at=started_at,
            )
            await unit_of_work.commit()
        return event

    async def _load_eligible_tools(
        self,
        command: RunTurnCommand,
        built_context: BuiltContext,
    ) -> tuple[EligibleTool, ...]:
        if built_context.turn_id != command.turn_id:
            raise RuntimeError("context builder returned a different turn")
        async with self._unit_of_work_factory() as unit_of_work:
            eligible = await unit_of_work.tools.list_eligible_for_agent(
                team_id=command.team_id,
                agent_version_id=built_context.agent_version_id,
            )
        granted = set(command.granted_scopes)
        return tuple(
            tool
            for tool in eligible
            if tool.version.manifest.effect is ToolEffect.READ_ONLY
            and set(tool.version.manifest.required_scopes).issubset(granted)
        )[:MAX_MODEL_TOOL_CANDIDATES]

    @staticmethod
    def _model_request(
        built_context: BuiltContext,
        eligible_tools: tuple[EligibleTool, ...],
    ) -> ModelRequest:
        return ModelRequest(
            phase=ModelRequestPhase.INITIAL,
            context=tuple(
                ModelContextItem(
                    kind=item.kind,
                    content=item.content,
                    role=item.role,
                )
                for item in built_context.items
            ),
            tools=tuple(
                ModelToolDescriptor(
                    tool_definition_id=tool.definition.id,
                    tool_version_id=tool.version.id,
                    tool_key=tool.definition.tool_key,
                    display_name=tool.version.manifest.display_name,
                    description=tool.version.manifest.description,
                    input_schema=tool.version.manifest.input_schema,
                )
                for tool in eligible_tools
            ),
        )

    async def _append_delta(self, *, command: RunTurnCommand, chunk: str) -> ExecutionEvent:
        occurred_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn, attempt = await self._load_execution(unit_of_work, command)
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
        command: RunTurnCommand,
        assistant_message_id: MessageId,
        response_text: str,
        chunk_count: int,
    ) -> ExecutionEvent:
        completed_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn, attempt = await self._load_execution(unit_of_work, command)
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
                kind=ExecutionEventKind.TURN_COMPLETED,
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

    @staticmethod
    async def _load_execution(
        unit_of_work: UnitOfWork,
        command: RunTurnCommand,
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

    async def _record_failure_safely(self, command: RunTurnCommand) -> None:
        try:
            await self._record_failure(command)
        except Exception:
            logger.exception(
                "failed to record terminal agent failure for turn %s attempt %s",
                command.turn_id,
                command.attempt_id,
            )

    async def _record_failure(self, command: RunTurnCommand) -> None:
        failed_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(command.turn_id)
            attempt = await unit_of_work.turns.get_attempt(command.attempt_id)
            if turn is None or attempt is None or attempt.turn_id != turn.id:
                return
            if turn.status is not TurnStatus.RUNNING:
                return
            if attempt.status is not TurnAttemptStatus.RUNNING:
                return
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.fail(at=failed_at),
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.fail(at=failed_at, failure_code=_FAILURE_CODE),
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_FAILED,
                payload={"failure_code": _FAILURE_CODE},
                occurred_at=failed_at,
            )
            await unit_of_work.commit()
