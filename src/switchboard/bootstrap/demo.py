"""Deterministic Phase 1 demonstration journeys."""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from time import perf_counter
from typing import cast
from uuid import UUID

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import ConversationApiServices
from switchboard.adapters.context.deterministic_summarizer import (
    DeterministicPrefixSummarizer,
)
from switchboard.adapters.models.deterministic import ScriptedModelGateway
from switchboard.adapters.orchestration.langgraph import LangGraphAgentOrchestrator
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.streaming.asyncio_sleeper import AsyncioSleeper
from switchboard.adapters.tools.reference import SearchWorkItemsAdapter
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.ports.model_gateway import CallTool, Respond
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.application.use_cases.build_turn_context import (
    BuildTurnContext,
    BuildTurnContextCommand,
)
from switchboard.application.use_cases.continue_conversation import ContinueConversation
from switchboard.application.use_cases.read_conversations import (
    GetConversation,
    GetTurn,
    ListConversationMessages,
)
from switchboard.application.use_cases.run_turn import RunTurn, RunTurnCommand, RunTurnResult
from switchboard.application.use_cases.start_conversation import StartConversation
from switchboard.bootstrap.config import Settings, load_settings
from switchboard.bootstrap.demo_environment import (
    DEMO_ACTOR_ID,
    DEMO_AGENT_VERSION_ID,
    DEMO_SEARCH_VERSION_ID,
    DEMO_TEAM_ID,
    FixedClock,
    SequenceIdGenerator,
    inspect_demo_seed,
    require_safe_demo_reset,
    validate_demo_environment,
)
from switchboard.domain.context import BuiltContext, ContextItemCandidate
from switchboard.domain.identifiers import (
    ApprovalRequestId,
    CommandReceiptId,
    ConversationId,
    ConversationSummaryId,
    ExecutionEventId,
    MessageId,
    PolicyEvaluationId,
    ToolInvocationId,
    TurnAttemptId,
    TurnId,
)

READ_ONLY_PROMPT = "Find launch work and summarize its status."
READ_ONLY_RESPONSE = "The launch checklist is open."
READ_ONLY_IDEMPOTENCY_KEY = "phase-1-read-only-v1"

READ_ONLY_CONVERSATION_ID = ConversationId(UUID("70000000-0000-4000-8000-000000000001"))
READ_ONLY_USER_MESSAGE_ID = MessageId(UUID("70000000-0000-4000-8000-000000000002"))
READ_ONLY_TURN_ID = TurnId(UUID("70000000-0000-4000-8000-000000000003"))
READ_ONLY_ATTEMPT_ID = TurnAttemptId(UUID("70000000-0000-4000-8000-000000000004"))
READ_ONLY_RECEIPT_ID = CommandReceiptId(UUID("70000000-0000-4000-8000-000000000005"))
READ_ONLY_SUMMARY_ID = ConversationSummaryId(UUID("70000000-0000-4000-8000-000000000006"))
READ_ONLY_ASSISTANT_MESSAGE_ID = MessageId(UUID("80000000-0000-4000-8000-000000000001"))
READ_ONLY_INVOCATION_ID = ToolInvocationId(UUID("80000000-0000-4000-8000-000000000002"))
READ_ONLY_POLICY_EVALUATION_ID = PolicyEvaluationId(UUID("80000000-0000-4000-8000-000000000003"))
READ_ONLY_APPROVAL_ID = ApprovalRequestId(UUID("80000000-0000-4000-8000-000000000004"))
READ_ONLY_EVENT_IDS = tuple(
    ExecutionEventId(UUID(f"90000000-0000-4000-8000-{index:012d}")) for index in range(1, 11)
)
WORKFLOW_USER_MESSAGE_ID = MessageId(UUID("71000000-0000-4000-8000-000000000001"))
WORKFLOW_TURN_ID = TurnId(UUID("71000000-0000-4000-8000-000000000002"))
WORKFLOW_ATTEMPT_ID = TurnAttemptId(UUID("71000000-0000-4000-8000-000000000003"))
WORKFLOW_RECEIPT_ID = CommandReceiptId(UUID("71000000-0000-4000-8000-000000000004"))


class DemoJourneyError(RuntimeError):
    """Raised when a deterministic journey cannot prove its expected contract."""


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage: str
    milliseconds: float


@dataclass(frozen=True, slots=True)
class ReadOnlyJourneyResult:
    conversation_id: ConversationId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    agent_version_id: str
    context_used_tokens: int
    context_available_tokens: int
    event_names: tuple[str, ...]
    disconnect_after_sequence: int
    reconnect_sequences: tuple[int, ...]
    reconstructed_response: str
    history_sequences: tuple[int, ...]
    measurement_environment: str
    time_to_first_committed_event_ms: float
    stage_timings: tuple[StageTiming, ...]


@dataclass(frozen=True, slots=True)
class SseFrame:
    sequence: int
    event: str
    data: dict[str, object]


class CharacterTokenCounter:
    """Deterministic development counter used by the local journey."""

    @property
    def version(self) -> str:
        return "character-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content)


class RecordingContextBuilder:
    """Capture the bounded context evidence produced for one demo turn."""

    def __init__(self, delegate: BuildTurnContext) -> None:
        self._delegate = delegate
        self.last_built: BuiltContext | None = None

    async def execute(self, command: BuildTurnContextCommand) -> BuiltContext:
        built = await self._delegate.execute(command)
        self.last_built = built
        return built


async def run_read_only_journey(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    settings: Settings,
) -> ReadOnlyJourneyResult:
    """Run one API-created, explicitly executed, reconnectable read-only turn."""

    seed = await inspect_demo_seed(unit_of_work_factory)
    if not seed.ready:
        raise DemoJourneyError("deterministic seed is not ready; run reset and seed first")
    async with unit_of_work_factory() as unit_of_work:
        existing_turn = await unit_of_work.turns.get(READ_ONLY_TURN_ID)
    if existing_turn is not None:
        raise DemoJourneyError("read-only journey already exists; run reset and seed first")

    conversation_services = demo_conversation_services(unit_of_work_factory)
    app = create_app(
        settings=settings,
        readiness_service=ReadinessService(probes=()),
        replay_turn_events=ReplayTurnEvents(
            unit_of_work_factory=unit_of_work_factory,
            sleeper=AsyncioSleeper(),
        ),
        conversation_api_services=conversation_services,
    )
    transport = ASGITransport(app=app)
    timings: list[StageTiming] = []
    journey_started = perf_counter()

    async with AsyncClient(transport=transport, base_url="http://switchboard.demo") as client:
        started = perf_counter()
        accepted_response = await client.post(
            "/api/v1/conversations",
            headers={
                "X-Team-ID": str(DEMO_TEAM_ID),
                "Idempotency-Key": READ_ONLY_IDEMPOTENCY_KEY,
            },
            json={
                "agent_version_id": str(DEMO_AGENT_VERSION_ID),
                "initial_user_message": READ_ONLY_PROMPT,
            },
        )
        timings.append(_timing("api_acceptance", started))
        _require_status(accepted_response, 202, stage="conversation acceptance")
        accepted = _json_object(accepted_response)
        _require_identity(accepted, "conversation_id", READ_ONLY_CONVERSATION_ID)
        _require_identity(accepted, "turn_id", READ_ONLY_TURN_ID)

        context_builder = _context_builder(unit_of_work_factory)
        started = perf_counter()
        run_result, first_event_ms = await _run_observing_first_event(
            unit_of_work_factory,
            runner=_run_turn(unit_of_work_factory, context_builder),
            command=RunTurnCommand(
                team_id=DEMO_TEAM_ID,
                actor_id=DEMO_ACTOR_ID,
                turn_id=READ_ONLY_TURN_ID,
                attempt_id=READ_ONLY_ATTEMPT_ID,
                granted_scopes=("work_items:read",),
            ),
        )
        timings.append(_timing("trusted_execution", started))
        if (
            not run_result.tool_called
            or run_result.assistant_message_id != READ_ONLY_ASSISTANT_MESSAGE_ID
        ):
            raise DemoJourneyError("trusted execution did not complete the read-only tool journey")
        built_context = context_builder.last_built
        if built_context is None:
            raise DemoJourneyError("trusted execution produced no bounded context evidence")

        events_url = _require_string(accepted, "events_url")
        started = perf_counter()
        first_delivery = await _consume_until_first_delta(
            client,
            events_url=events_url,
        )
        timings.append(_timing("initial_event_stream", started))
        disconnect_sequence = first_delivery[-1].sequence

        started = perf_counter()
        reconnect = await client.get(
            events_url,
            headers={
                "X-Team-ID": str(DEMO_TEAM_ID),
                "Last-Event-ID": str(disconnect_sequence),
            },
        )
        timings.append(_timing("event_reconnect", started))
        _require_status(reconnect, 200, stage="event reconnect")
        reconnect_frames = parse_sse(reconnect.text)
        delivered = (*first_delivery, *reconnect_frames)
        if tuple(frame.sequence for frame in delivered) != tuple(
            range(1, delivered[-1].sequence + 1)
        ) or delivered[-1].event not in {"turn.completed", "turn.failed"}:
            raise DemoJourneyError("reconnect did not reconstruct the exact event sequence")
        reconstructed = "".join(
            _require_string(frame.data, "text")
            for frame in delivered
            if frame.event == "response.delta"
        )
        if reconstructed != READ_ONLY_RESPONSE:
            raise DemoJourneyError("response deltas did not reconstruct the assistant response")

        started = perf_counter()
        history = await client.get(
            f"/api/v1/conversations/{READ_ONLY_CONVERSATION_ID}/messages",
            headers={"X-Team-ID": str(DEMO_TEAM_ID)},
        )
        turn = await client.get(
            f"/api/v1/turns/{READ_ONLY_TURN_ID}",
            headers={"X-Team-ID": str(DEMO_TEAM_ID)},
        )
        timings.append(_timing("history_verification", started))
        _require_status(history, 200, stage="ordered history")
        _require_status(turn, 200, stage="turn inspection")
        history_sequences = _verify_history(_json_object(history))
        turn_body = _json_object(turn)
        if _require_string(turn_body, "agent_version_id") != str(DEMO_AGENT_VERSION_ID):
            raise DemoJourneyError("turn inspection returned the wrong pinned agent version")

    timings.append(_timing("total", journey_started))
    return ReadOnlyJourneyResult(
        conversation_id=READ_ONLY_CONVERSATION_ID,
        turn_id=READ_ONLY_TURN_ID,
        attempt_id=READ_ONLY_ATTEMPT_ID,
        agent_version_id=str(DEMO_AGENT_VERSION_ID),
        context_used_tokens=built_context.used_input_tokens,
        context_available_tokens=built_context.policy.available_input_tokens,
        event_names=tuple(frame.event for frame in delivered),
        disconnect_after_sequence=disconnect_sequence,
        reconnect_sequences=tuple(frame.sequence for frame in reconnect_frames),
        reconstructed_response=reconstructed,
        history_sequences=history_sequences,
        measurement_environment=settings.environment,
        time_to_first_committed_event_ms=first_event_ms,
        stage_timings=tuple(timings),
    )


async def _run_observing_first_event(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    runner: RunTurn,
    command: RunTurnCommand,
) -> tuple[RunTurnResult, float]:
    """Run a turn and record a local upper bound for observing its first commit."""

    started = perf_counter()
    execution = asyncio.create_task(runner.execute(command))
    observed_ms: float | None = None
    while not execution.done():
        async with unit_of_work_factory() as unit_of_work:
            events = await unit_of_work.turns.list_events(
                turn_id=command.turn_id,
                after_sequence=0,
                limit=1,
            )
        if events:
            observed_ms = round((perf_counter() - started) * 1000, 3)
            break
        await asyncio.sleep(0.001)

    result = await execution
    if observed_ms is None:
        async with unit_of_work_factory() as unit_of_work:
            events = await unit_of_work.turns.list_events(
                turn_id=command.turn_id,
                after_sequence=0,
                limit=1,
            )
        if not events:
            raise DemoJourneyError("trusted execution committed no observable event")
        observed_ms = round((perf_counter() - started) * 1000, 3)
    return result, observed_ms


def parse_sse(value: str) -> tuple[SseFrame, ...]:
    """Parse the compact SSE shape emitted by the Switchboard v1 endpoint."""

    frames: list[SseFrame] = []
    for raw_frame in value.split("\n\n"):
        if not raw_frame:
            continue
        lines = raw_frame.splitlines()
        if len(lines) != 3:
            raise DemoJourneyError("event stream contained an unexpected frame shape")
        try:
            sequence = int(lines[0].removeprefix("id: "))
        except ValueError as error:
            raise DemoJourneyError("event stream contained an invalid sequence") from error
        event = lines[1].removeprefix("event: ")
        data_value = json.loads(lines[2].removeprefix("data: "))
        if not isinstance(data_value, dict) or not all(isinstance(key, str) for key in data_value):
            raise DemoJourneyError("event stream contained a non-object payload")
        frames.append(SseFrame(sequence, event, cast(dict[str, object], data_value)))
    return tuple(frames)


async def _consume_until_first_delta(
    client: AsyncClient,
    *,
    events_url: str,
) -> tuple[SseFrame, ...]:
    frames: list[SseFrame] = []
    buffered_lines: list[str] = []
    async with client.stream(
        "GET",
        events_url,
        headers={"X-Team-ID": str(DEMO_TEAM_ID)},
    ) as response:
        _require_status(response, 200, stage="initial event stream")
        async for line in response.aiter_lines():
            if line:
                buffered_lines.append(line)
                continue
            parsed = parse_sse("\n".join(buffered_lines) + "\n\n")
            if len(parsed) != 1:
                raise DemoJourneyError("initial event stream produced an invalid frame")
            frames.append(parsed[0])
            buffered_lines.clear()
            if frames[-1].event == "response.delta":
                break
    if not frames or frames[-1].event != "response.delta":
        raise DemoJourneyError("initial event stream contained no response delta")
    return tuple(frames)


def demo_conversation_services(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> ConversationApiServices:
    clock = FixedClock()
    return ConversationApiServices(
        start_conversation=StartConversation(
            unit_of_work_factory=unit_of_work_factory,
            clock=clock,
            conversation_ids=SequenceIdGenerator((READ_ONLY_CONVERSATION_ID,)),
            message_ids=SequenceIdGenerator((READ_ONLY_USER_MESSAGE_ID,)),
            turn_ids=SequenceIdGenerator((READ_ONLY_TURN_ID,)),
            attempt_ids=SequenceIdGenerator((READ_ONLY_ATTEMPT_ID,)),
            receipt_ids=SequenceIdGenerator((READ_ONLY_RECEIPT_ID,)),
        ),
        continue_conversation=ContinueConversation(
            unit_of_work_factory=unit_of_work_factory,
            clock=clock,
            message_ids=SequenceIdGenerator((WORKFLOW_USER_MESSAGE_ID,)),
            turn_ids=SequenceIdGenerator((WORKFLOW_TURN_ID,)),
            attempt_ids=SequenceIdGenerator((WORKFLOW_ATTEMPT_ID,)),
            receipt_ids=SequenceIdGenerator((WORKFLOW_RECEIPT_ID,)),
        ),
        get_conversation=GetConversation(unit_of_work_factory=unit_of_work_factory),
        list_messages=ListConversationMessages(unit_of_work_factory=unit_of_work_factory),
        get_turn=GetTurn(unit_of_work_factory=unit_of_work_factory),
    )


def _context_builder(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> RecordingContextBuilder:
    counter = CharacterTokenCounter()
    return RecordingContextBuilder(
        BuildTurnContext(
            unit_of_work_factory=unit_of_work_factory,
            token_counter=counter,
            summarizer=DeterministicPrefixSummarizer(counter),
            clock=FixedClock(),
            summary_ids=SequenceIdGenerator((READ_ONLY_SUMMARY_ID,)),
        )
    )


def _run_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    context_builder: RecordingContextBuilder,
) -> RunTurn:
    gateway = ScriptedModelGateway(
        (
            CallTool(DEMO_SEARCH_VERSION_ID, {"query": "launch", "limit": 5}),
            Respond(READ_ONLY_RESPONSE),
        )
    )
    return RunTurn(
        unit_of_work_factory=unit_of_work_factory,
        context_builder=context_builder,
        orchestrator=LangGraphAgentOrchestrator(model_gateway=gateway),
        adapter_resolver=StaticToolAdapterResolver(
            {"reference.search_work_items.v1": SearchWorkItemsAdapter()}
        ),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        invocation_ids=SequenceIdGenerator((READ_ONLY_INVOCATION_ID,)),
        policy_evaluation_ids=SequenceIdGenerator((READ_ONLY_POLICY_EVALUATION_ID,)),
        approval_ids=SequenceIdGenerator((READ_ONLY_APPROVAL_ID,)),
        event_ids=SequenceIdGenerator(READ_ONLY_EVENT_IDS),
        message_ids=SequenceIdGenerator((READ_ONLY_ASSISTANT_MESSAGE_ID,)),
    )


def _json_object(response: Response) -> dict[str, object]:
    value = json.loads(response.text)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DemoJourneyError("API returned a non-object response")
    return cast(dict[str, object], value)


def _require_status(response: Response, expected: int, *, stage: str) -> None:
    if response.status_code != expected:
        raise DemoJourneyError(f"{stage} returned HTTP {response.status_code}")


def _require_string(value: dict[str, object], key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str):
        raise DemoJourneyError(f"expected string field {key}")
    return selected


def _require_identity(value: dict[str, object], key: str, expected: object) -> None:
    if _require_string(value, key) != str(expected):
        raise DemoJourneyError(f"API returned an unexpected {key}")


def _verify_history(value: dict[str, object]) -> tuple[int, ...]:
    items = value.get("items")
    if not isinstance(items, list) or len(items) != 2:
        raise DemoJourneyError("history did not contain exactly two visible messages")
    expected = ((1, "user", READ_ONLY_PROMPT), (2, "assistant", READ_ONLY_RESPONSE))
    sequences: list[int] = []
    for raw, (sequence, role, content) in zip(items, expected, strict=True):
        if not isinstance(raw, dict):
            raise DemoJourneyError("history contained an invalid message")
        item = cast(dict[str, object], raw)
        if (
            item.get("sequence") != sequence
            or item.get("role") != role
            or item.get("content") != content
        ):
            raise DemoJourneyError("history order or content differed from the journey")
        sequences.append(sequence)
    return tuple(sequences)


def _timing(stage: str, started: float) -> StageTiming:
    return StageTiming(stage=stage, milliseconds=round((perf_counter() - started) * 1000, 3))


async def _run_command(journey: str) -> dict[str, object]:
    settings = load_settings()
    require_safe_demo_reset(settings)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    try:
        await validate_demo_environment(engine, unit_of_work_factory)
        if journey == "read-only":
            result = await run_read_only_journey(
                unit_of_work_factory,
                settings=settings,
            )
            output = _read_only_output(result)
        else:
            from switchboard.bootstrap.demo_workflow import run_approval_workflow_journey

            recreated_factory = SqlAlchemyUnitOfWorkFactory(
                async_sessionmaker(engine, expire_on_commit=False)
            )
            workflow_result = await run_approval_workflow_journey(
                unit_of_work_factory,
                recreated_factory=recreated_factory,
                settings=settings,
            )
            output = workflow_result.to_safe_output()
    finally:
        await engine.dispose()
    return output


def _read_only_output(result: ReadOnlyJourneyResult) -> dict[str, object]:
    return {
        "journey": "read-only",
        "conversation_id": str(result.conversation_id),
        "turn_id": str(result.turn_id),
        "attempt_id": str(result.attempt_id),
        "agent_version_id": result.agent_version_id,
        "context": {
            "used_tokens": result.context_used_tokens,
            "available_tokens": result.context_available_tokens,
        },
        "events": result.event_names,
        "disconnect_after_sequence": result.disconnect_after_sequence,
        "reconnect_sequences": result.reconnect_sequences,
        "reconstructed_response": result.reconstructed_response,
        "history_sequences": result.history_sequences,
        "measurement": {
            "environment": result.measurement_environment,
            "sample_size": 1,
            "scope": "single deterministic development journey",
            "production_capacity_claim": False,
            "time_to_first_committed_event_observed_ms": (result.time_to_first_committed_event_ms),
            "stage_timings_ms": {
                timing.stage: timing.milliseconds for timing in result.stage_timings
            },
        },
    }


def main() -> None:
    """Run one selected deterministic Phase 1 journey."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journey", choices=("read-only", "approval-workflow"))
    args = parser.parse_args()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        output = asyncio.run(_run_command(args.journey), loop_factory=loop_factory)
    except (DemoJourneyError, RuntimeError) as error:
        parser.exit(1, f"demo journey error: {error}\n")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
