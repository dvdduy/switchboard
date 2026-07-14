"""Durable policy gate and dispatch for one model-requested tool call."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from switchboard.application.errors import (
    ToolDispatchError,
    TurnAttemptMismatchError,
    TurnAttemptNotFoundError,
    TurnNotFoundError,
)
from switchboard.application.ports.agent_orchestrator import ToolCallAwaitingApproval
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.model_gateway import CallTool, ModelToolResult
from switchboard.application.ports.tool_adapter import (
    ToolAdapterResolver,
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
)
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    PolicyEvaluationRecord,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    ApprovalRequestId,
    ExecutionEventId,
    PolicyEvaluationId,
    TeamId,
    ToolInvocationId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.policy import (
    PolicyContext,
    PolicyDecision,
    PolicyEnvironment,
    evaluate_policy,
    fingerprint_action,
    summarize_action,
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
_POLICY_DENIED = "tool_policy_denied"

DEFAULT_APPROVAL_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class ToolDispatchContext:
    """Trusted execution identity and authority for one running attempt."""

    team_id: TeamId
    actor_id: ActorId
    agent_version_id: AgentVersionId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    granted_scopes: tuple[str, ...]
    environment: PolicyEnvironment = PolicyEnvironment.DEVELOPMENT

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
        policy_evaluation_ids: IdGenerator[PolicyEvaluationId],
        approval_ids: IdGenerator[ApprovalRequestId],
        event_ids: IdGenerator[ExecutionEventId],
        approval_ttl: timedelta = DEFAULT_APPROVAL_TTL,
    ) -> None:
        self._context = context
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._clock = clock
        self._invocation_ids = invocation_ids
        self._policy_evaluation_ids = policy_evaluation_ids
        self._approval_ids = approval_ids
        self._event_ids = event_ids
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._approval_ttl = approval_ttl

    async def execute(
        self,
        action: CallTool,
    ) -> ModelToolResult | ToolCallAwaitingApproval:
        eligible = await self._preflight(action)
        policy_context = self._policy_context(eligible, action)
        policy_result = evaluate_policy(policy_context)
        fingerprint = fingerprint_action(policy_context)
        evaluated_at = self._clock.now()
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
        evaluation = PolicyEvaluationRecord(
            id=self._policy_evaluation_ids.new(),
            team_id=policy_context.team_id,
            requester_actor_id=policy_context.actor_id,
            agent_version_id=policy_context.agent_version_id,
            turn_id=self._context.turn_id,
            attempt_id=self._context.attempt_id,
            invocation_id=(
                None if policy_result.decision is PolicyDecision.DENY else invocation.id
            ),
            tool_definition_id=policy_context.tool_definition_id,
            tool_version_id=policy_context.tool_version_id,
            effect=policy_context.effect,
            environment=policy_context.environment,
            required_scopes=policy_context.required_scopes,
            granted_scopes=policy_context.granted_scopes,
            evaluation=policy_result,
            fingerprint=fingerprint,
            evaluated_at=evaluated_at,
        )
        if policy_result.decision is PolicyDecision.DENY:
            await self._persist_denied_evaluation(evaluation)
            raise ToolDispatchError(_POLICY_DENIED)
        if policy_result.decision is PolicyDecision.REQUIRE_CONFIRMATION:
            approval = ApprovalRequest(
                id=self._approval_ids.new(),
                team_id=self._context.team_id,
                policy_evaluation_id=evaluation.id,
                invocation_id=invocation.id,
                requester_actor_id=self._context.actor_id,
                fingerprint=fingerprint,
                safe_summary=summarize_action(policy_context),
                status=ApprovalStatus.PENDING,
                created_at=evaluated_at,
                expires_at=evaluated_at + self._approval_ttl,
            )
            return await self._persist_pause(
                invocation=invocation,
                evaluation=evaluation,
                approval=approval,
            )

        adapter = self._adapter_resolver.resolve(eligible.version.manifest.adapter_key)
        if adapter is None:
            raise ToolDispatchError(_ADAPTER_UNAVAILABLE)
        await self._persist_pending(invocation, evaluation)
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

    async def _preflight(self, action: CallTool) -> EligibleTool:
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
        if not set(eligible.version.manifest.required_scopes).issubset(
            self._context.granted_scopes
        ):
            raise ToolDispatchError(_SCOPE_DENIED)
        if self._schema_validator.validate_instance(
            instance=action.arguments,
            schema=eligible.version.manifest.input_schema,
        ):
            raise ToolDispatchError(_ARGUMENTS_INVALID)
        return eligible

    async def _persist_pending(
        self,
        invocation: ToolInvocation,
        evaluation: PolicyEvaluationRecord,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.add(invocation)
            await unit_of_work.approvals.add_evaluation(evaluation)
            await unit_of_work.commit()

    async def _persist_denied_evaluation(
        self,
        evaluation: PolicyEvaluationRecord,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.approvals.add_evaluation(evaluation)
            await unit_of_work.commit()

    async def _persist_pause(
        self,
        *,
        invocation: ToolInvocation,
        evaluation: PolicyEvaluationRecord,
        approval: ApprovalRequest,
    ) -> ToolCallAwaitingApproval:
        awaiting_invocation = invocation.await_confirmation()
        paused_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(self._context.turn_id)
            attempt = await unit_of_work.turns.get_attempt(self._context.attempt_id)
            if (
                turn is None
                or attempt is None
                or attempt.turn_id != turn.id
                or turn.status is not TurnStatus.RUNNING
                or attempt.status is not TurnAttemptStatus.RUNNING
            ):
                raise ToolDispatchError(_EXECUTION_NOT_RUNNING)
            await unit_of_work.tool_invocations.add(invocation)
            await unit_of_work.approvals.add_evaluation(evaluation)
            await unit_of_work.approvals.add_request(approval)
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=invocation,
                updated=awaiting_invocation,
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.await_confirmation(),
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.await_confirmation(),
            )
            event = await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.APPROVAL_REQUIRED,
                payload={
                    "approval_id": str(approval.id),
                    "invocation_id": str(invocation.id),
                    "tool_definition_id": str(invocation.tool_definition_id),
                    "tool_version_id": str(invocation.tool_version_id),
                    "expires_at": approval.expires_at.isoformat(),
                    "fingerprint_version": approval.fingerprint.version,
                    "safe_summary": {
                        "tool_definition_id": str(approval.safe_summary.tool_definition_id),
                        "tool_version_id": str(approval.safe_summary.tool_version_id),
                        "effect": approval.safe_summary.effect.value,
                        "argument_fields": list(approval.safe_summary.argument_fields),
                    },
                },
                occurred_at=paused_at,
            )
            await unit_of_work.commit()
        return ToolCallAwaitingApproval(
            approval_id=approval.id,
            invocation_id=invocation.id,
            event_sequence=event.sequence,
        )

    def _policy_context(
        self,
        eligible: EligibleTool,
        action: CallTool,
    ) -> PolicyContext:
        return PolicyContext(
            team_id=self._context.team_id,
            actor_id=self._context.actor_id,
            agent_version_id=self._context.agent_version_id,
            tool_team_id=eligible.definition.team_id,
            tool_definition_id=eligible.definition.id,
            tool_version_id=eligible.version.id,
            effect=eligible.version.manifest.effect,
            required_scopes=eligible.version.manifest.required_scopes,
            granted_scopes=self._context.granted_scopes,
            environment=self._context.environment,
            arguments=action.arguments,
            is_bound=True,
            is_active=True,
            is_conformant=True,
        )

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
            version = await unit_of_work.tools.get_version(pending.tool_version_id)
            if version is None or version.manifest.effect is not ToolEffect.READ_ONLY:
                raise ToolDispatchError(_EFFECT_NOT_ALLOWED)
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
