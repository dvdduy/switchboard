"""Durable, framework-independent dispatch of one read-only tool call."""

import asyncio
from dataclasses import dataclass

from switchboard.application.errors import (
    ToolDispatchError,
    TurnAttemptMismatchError,
    TurnAttemptNotFoundError,
    TurnNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.model_gateway import CallTool, ModelToolResult
from switchboard.application.ports.tool_adapter import (
    ToolAdapter,
    ToolAdapterResolver,
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
)
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    AgentVersionId,
    ExecutionEventId,
    TeamId,
    ToolInvocationId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import EligibleTool, ToolEffect, ToolLifecycleStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus

_ARGUMENTS_INVALID = "tool_arguments_invalid"
_OUTPUT_INVALID = "tool_output_invalid"
_ADAPTER_ERROR = "tool_adapter_error"
_ADAPTER_UNAVAILABLE = "tool_adapter_unavailable"
_TIMEOUT = "tool_timeout"
_NOT_ELIGIBLE = "tool_not_eligible"
_EFFECT_NOT_ALLOWED = "tool_effect_not_allowed"
_SCOPE_DENIED = "tool_scope_denied"
_EXECUTION_NOT_RUNNING = "tool_execution_not_running"


@dataclass(frozen=True, slots=True)
class ToolDispatchContext:
    """Trusted execution identity and authority for one running attempt."""

    team_id: TeamId
    agent_version_id: AgentVersionId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    granted_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "granted_scopes", tuple(sorted(set(self.granted_scopes))))


class DurableToolCallHandler:
    """Validate, durably record, and execute one exact read-only tool version."""

    def __init__(
        self,
        *,
        context: ToolDispatchContext,
        unit_of_work_factory: UnitOfWorkFactory,
        adapter_resolver: ToolAdapterResolver,
        schema_validator: JsonSchemaValidator,
        clock: Clock,
        invocation_ids: IdGenerator[ToolInvocationId],
        event_ids: IdGenerator[ExecutionEventId],
    ) -> None:
        self._context = context
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._clock = clock
        self._invocation_ids = invocation_ids
        self._event_ids = event_ids

    async def execute(self, action: CallTool) -> ModelToolResult:
        eligible, adapter = await self._preflight(action)
        invocation_id = self._invocation_ids.new()
        invocation = ToolInvocation(
            id=invocation_id,
            turn_id=self._context.turn_id,
            attempt_id=self._context.attempt_id,
            invocation_number=1,
            tool_definition_id=eligible.definition.id,
            tool_version_id=eligible.version.id,
            arguments=action.arguments,
            idempotency_key=f"invocation:{invocation_id}",
            authorized_scopes=eligible.version.manifest.required_scopes,
            status=ToolInvocationStatus.PENDING,
            created_at=self._clock.now(),
        )
        await self._persist_pending(invocation)
        running = await self._start_if_still_eligible(invocation)

        request = ToolInvocationRequest(
            arguments=running.arguments,
            idempotency_key=running.idempotency_key,
        )
        try:
            adapter_result = await asyncio.wait_for(
                adapter.invoke(request),
                timeout=eligible.version.manifest.timeout_ms / 1_000,
            )
        except TimeoutError:
            await self._fail(running, _TIMEOUT)
            raise ToolDispatchError(_TIMEOUT) from None
        except Exception:
            await self._fail(running, _ADAPTER_ERROR)
            raise ToolDispatchError(_ADAPTER_ERROR) from None

        if isinstance(adapter_result, ToolInvocationFailure):
            failure_code = f"tool.{adapter_result.error_code}"
            await self._fail(running, failure_code)
            raise ToolDispatchError(failure_code)
        if not isinstance(adapter_result, ToolInvocationSuccess):
            await self._fail(running, _ADAPTER_ERROR)
            raise ToolDispatchError(_ADAPTER_ERROR)

        if self._schema_validator.validate_instance(
            instance=adapter_result.output,
            schema=eligible.version.manifest.output_schema,
        ):
            await self._fail(running, _OUTPUT_INVALID)
            raise ToolDispatchError(_OUTPUT_INVALID)
        try:
            model_result = ModelToolResult(
                tool_version_id=running.tool_version_id,
                output=adapter_result.output,
            )
        except DomainValidationError:
            await self._fail(running, _OUTPUT_INVALID)
            raise ToolDispatchError(_OUTPUT_INVALID) from None

        await self._succeed(running, model_result)
        return model_result

    async def _preflight(self, action: CallTool) -> tuple[EligibleTool, ToolAdapter]:
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(self._context.turn_id)
            if turn is None:
                raise TurnNotFoundError(f"turn {self._context.turn_id} was not found")
            attempt = await unit_of_work.turns.get_attempt(self._context.attempt_id)
            if attempt is None:
                raise TurnAttemptNotFoundError(
                    f"turn attempt {self._context.attempt_id} was not found"
                )
            if attempt.turn_id != turn.id:
                raise TurnAttemptMismatchError(
                    f"turn attempt {attempt.id} does not belong to turn {turn.id}"
                )
            conversation = await unit_of_work.conversations.get(turn.conversation_id)
            if (
                conversation is None
                or conversation.team_id != self._context.team_id
                or turn.agent_version_id != self._context.agent_version_id
                or turn.status is not TurnStatus.RUNNING
                or attempt.status is not TurnAttemptStatus.RUNNING
            ):
                raise ToolDispatchError(_EXECUTION_NOT_RUNNING)
            eligible_tools = await unit_of_work.tools.list_eligible_for_agent(
                team_id=self._context.team_id,
                agent_version_id=self._context.agent_version_id,
            )

        eligible = next(
            (tool for tool in eligible_tools if tool.version.id == action.tool_version_id),
            None,
        )
        if eligible is None:
            raise ToolDispatchError(_NOT_ELIGIBLE)
        if eligible.version.manifest.effect is not ToolEffect.READ_ONLY:
            raise ToolDispatchError(_EFFECT_NOT_ALLOWED)
        if not set(eligible.version.manifest.required_scopes).issubset(
            self._context.granted_scopes
        ):
            raise ToolDispatchError(_SCOPE_DENIED)
        if self._schema_validator.validate_instance(
            instance=action.arguments,
            schema=eligible.version.manifest.input_schema,
        ):
            raise ToolDispatchError(_ARGUMENTS_INVALID)
        adapter = self._adapter_resolver.resolve(eligible.version.manifest.adapter_key)
        if adapter is None:
            raise ToolDispatchError(_ADAPTER_UNAVAILABLE)
        return eligible, adapter

    async def _persist_pending(self, invocation: ToolInvocation) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.add(invocation)
            await unit_of_work.commit()

    async def _start_if_still_eligible(self, pending: ToolInvocation) -> ToolInvocation:
        started_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.tools.get_version_state_for_update(pending.tool_version_id)
            if state is None or state.status is not ToolLifecycleStatus.ACTIVE:
                raise ToolDispatchError(_NOT_ELIGIBLE)
            eligible_tools = await unit_of_work.tools.list_eligible_for_agent(
                team_id=self._context.team_id,
                agent_version_id=self._context.agent_version_id,
            )
            if not any(
                tool.definition.id == pending.tool_definition_id
                and tool.version.id == pending.tool_version_id
                for tool in eligible_tools
            ):
                raise ToolDispatchError(_NOT_ELIGIBLE)
            running = pending.start(at=started_at)
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=pending,
                updated=running,
            )
            await unit_of_work.turns.append_event(
                turn_id=pending.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=pending.attempt_id,
                kind=ExecutionEventKind.TOOL_STARTED,
                payload={
                    "invocation_id": str(pending.id),
                    "tool_definition_id": str(pending.tool_definition_id),
                    "tool_version_id": str(pending.tool_version_id),
                },
                occurred_at=started_at,
            )
            await unit_of_work.commit()
        return running

    async def _succeed(self, running: ToolInvocation, result: ModelToolResult) -> None:
        completed_at = self._clock.now()
        succeeded = running.succeed(at=completed_at, result=result.output)
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=running,
                updated=succeeded,
            )
            await unit_of_work.turns.append_event(
                turn_id=running.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=running.attempt_id,
                kind=ExecutionEventKind.TOOL_COMPLETED,
                payload={"invocation_id": str(running.id)},
                occurred_at=completed_at,
            )
            await unit_of_work.commit()

    async def _fail(self, running: ToolInvocation, failure_code: str) -> None:
        completed_at = self._clock.now()
        failed = running.fail(at=completed_at, failure_code=failure_code)
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=running,
                updated=failed,
            )
            await unit_of_work.turns.append_event(
                turn_id=running.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=running.attempt_id,
                kind=ExecutionEventKind.TOOL_FAILED,
                payload={
                    "invocation_id": str(running.id),
                    "failure_code": failure_code,
                },
                occurred_at=completed_at,
            )
            await unit_of_work.commit()
