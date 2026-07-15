"""Persist, dispatch, and replay one durable workflow discovery step."""

import asyncio
from dataclasses import dataclass

from switchboard.application.errors import (
    ConversationNotFoundError,
    ToolDispatchError,
    TurnAttemptMismatchError,
    TurnAttemptNotFoundError,
    TurnNotFoundError,
    TurnTeamMismatchError,
    WorkflowDiscoveryConflictError,
    WorkflowDiscoveryInProgressError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.tool_adapter import (
    ToolAdapter,
    ToolAdapterResolver,
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
)
from switchboard.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from switchboard.domain.approvals import PolicyEvaluationRecord
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    ExecutionEventId,
    PolicyEvaluationId,
    TeamId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
    TurnWorkflowId,
    WorkflowStepId,
)
from switchboard.domain.json_values import JsonObject, freeze_json_object
from switchboard.domain.policy import (
    PolicyContext,
    PolicyDecision,
    PolicyEnvironment,
    evaluate_policy,
    fingerprint_action,
)
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import EligibleTool, ToolEffect, ToolLifecycleStatus
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import (
    TurnWorkflow,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepKind,
    WorkflowStepStatus,
)

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


@dataclass(frozen=True, slots=True)
class RunWorkflowDiscoveryCommand:
    """Trusted exact discovery request for one already-running turn attempt."""

    team_id: TeamId
    actor_id: ActorId
    agent_version_id: AgentVersionId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    tool_version_id: ToolVersionId
    arguments: JsonObject
    granted_scopes: tuple[str, ...]
    environment: PolicyEnvironment = PolicyEnvironment.DEVELOPMENT

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            freeze_json_object(self.arguments, field_name="arguments"),
        )
        object.__setattr__(self, "granted_scopes", tuple(sorted(set(self.granted_scopes))))


@dataclass(frozen=True, slots=True)
class RunWorkflowDiscoveryResult:
    """Committed discovery result, possibly replayed without adapter execution."""

    workflow_id: TurnWorkflowId
    step_id: WorkflowStepId
    invocation_id: ToolInvocationId
    output: JsonObject
    replayed: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output",
            freeze_json_object(self.output, field_name="output"),
        )


@dataclass(frozen=True, slots=True)
class _ClaimedDiscovery:
    workflow: TurnWorkflow
    step: WorkflowStep
    invocation: ToolInvocation
    eligible_tool: EligibleTool
    adapter: ToolAdapter


class RunWorkflowDiscovery:
    """Own one persisted discovery intent and advance it at most once logically."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        adapter_resolver: ToolAdapterResolver,
        schema_validator: JsonSchemaValidator,
        clock: Clock,
        workflow_ids: IdGenerator[TurnWorkflowId],
        step_ids: IdGenerator[WorkflowStepId],
        invocation_ids: IdGenerator[ToolInvocationId],
        policy_evaluation_ids: IdGenerator[PolicyEvaluationId],
        event_ids: IdGenerator[ExecutionEventId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._clock = clock
        self._workflow_ids = workflow_ids
        self._step_ids = step_ids
        self._invocation_ids = invocation_ids
        self._policy_evaluation_ids = policy_evaluation_ids
        self._event_ids = event_ids

    async def execute(
        self,
        command: RunWorkflowDiscoveryCommand,
    ) -> RunWorkflowDiscoveryResult:
        await self._persist_intent_if_absent(command)
        claim_or_replay = await self._claim_or_replay(command)
        if isinstance(claim_or_replay, RunWorkflowDiscoveryResult):
            return claim_or_replay

        claimed = claim_or_replay
        request = ToolInvocationRequest(
            arguments=claimed.invocation.arguments,
            idempotency_key=claimed.invocation.idempotency_key,
        )
        try:
            adapter_result = await asyncio.wait_for(
                claimed.adapter.invoke(request),
                timeout=claimed.eligible_tool.version.manifest.timeout_ms / 1_000,
            )
        except TimeoutError:
            await self._record_failure(claimed, _TIMEOUT)
            raise ToolDispatchError(_TIMEOUT) from None
        except Exception:
            await self._record_failure(claimed, _ADAPTER_ERROR)
            raise ToolDispatchError(_ADAPTER_ERROR) from None

        if isinstance(adapter_result, ToolInvocationFailure):
            failure_code = f"tool.{adapter_result.error_code}"
            await self._record_failure(claimed, failure_code)
            raise ToolDispatchError(failure_code)
        if not isinstance(adapter_result, ToolInvocationSuccess):
            await self._record_failure(claimed, _ADAPTER_ERROR)
            raise ToolDispatchError(_ADAPTER_ERROR)
        if self._schema_validator.validate_instance(
            instance=adapter_result.output,
            schema=claimed.eligible_tool.version.manifest.output_schema,
        ):
            await self._record_failure(claimed, _OUTPUT_INVALID)
            raise ToolDispatchError(_OUTPUT_INVALID)

        return await self._record_success(claimed, adapter_result.output)

    async def _persist_intent_if_absent(
        self,
        command: RunWorkflowDiscoveryCommand,
    ) -> None:
        created_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get_for_update(command.turn_id)
            attempt = await unit_of_work.turns.get_attempt(command.attempt_id)
            await self._validate_execution(unit_of_work, command, turn, attempt)

            existing = await unit_of_work.workflows.get_for_turn(command.turn_id)
            if existing is not None:
                return

            eligible = await self._load_eligible(unit_of_work, command)
            adapter = self._adapter_resolver.resolve(eligible.version.manifest.adapter_key)
            if adapter is None:
                raise ToolDispatchError(_ADAPTER_UNAVAILABLE)
            del adapter

            policy_context = self._policy_context(command, eligible)
            policy_result = evaluate_policy(policy_context)
            if policy_result.decision is not PolicyDecision.ALLOW:
                raise ToolDispatchError(_POLICY_DENIED)

            existing_invocations = await unit_of_work.tool_invocations.list_for_turn(
                command.turn_id
            )
            invocation_number = (
                1 if not existing_invocations else existing_invocations[-1].invocation_number + 1
            )
            workflow = TurnWorkflow(
                id=self._workflow_ids.new(),
                turn_id=command.turn_id,
                attempt_id=command.attempt_id,
                status=WorkflowStatus.DISCOVERY_PENDING,
                plan_version=1,
                created_at=created_at,
                updated_at=created_at,
            )
            invocation_id = self._invocation_ids.new()
            invocation = ToolInvocation(
                id=invocation_id,
                turn_id=command.turn_id,
                attempt_id=command.attempt_id,
                invocation_number=invocation_number,
                tool_definition_id=eligible.definition.id,
                tool_version_id=eligible.version.id,
                arguments=command.arguments,
                idempotency_key=f"invocation:{invocation_id}",
                authorized_scopes=eligible.version.manifest.required_scopes,
                status=ToolInvocationStatus.PENDING,
                created_at=created_at,
            )
            step = WorkflowStep(
                id=self._step_ids.new(),
                workflow_id=workflow.id,
                turn_id=workflow.turn_id,
                attempt_id=workflow.attempt_id,
                step_number=1,
                kind=WorkflowStepKind.DISCOVERY_TOOL,
                status=WorkflowStepStatus.PENDING,
                invocation_id=invocation.id,
                created_at=created_at,
            )
            evaluation = PolicyEvaluationRecord(
                id=self._policy_evaluation_ids.new(),
                team_id=command.team_id,
                requester_actor_id=command.actor_id,
                agent_version_id=command.agent_version_id,
                turn_id=command.turn_id,
                attempt_id=command.attempt_id,
                invocation_id=invocation.id,
                tool_definition_id=eligible.definition.id,
                tool_version_id=eligible.version.id,
                effect=eligible.version.manifest.effect,
                environment=command.environment,
                required_scopes=eligible.version.manifest.required_scopes,
                granted_scopes=command.granted_scopes,
                evaluation=policy_result,
                fingerprint=fingerprint_action(policy_context),
                evaluated_at=created_at,
            )
            await unit_of_work.tool_invocations.add(invocation)
            await unit_of_work.approvals.add_evaluation(evaluation)
            await unit_of_work.workflows.add(workflow)
            await unit_of_work.workflows.add_step(step)
            await unit_of_work.commit()

    async def _claim_or_replay(
        self,
        command: RunWorkflowDiscoveryCommand,
    ) -> _ClaimedDiscovery | RunWorkflowDiscoveryResult:
        started_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            workflow = await unit_of_work.workflows.get_for_turn_for_update(command.turn_id)
            if workflow is None:
                raise RuntimeError("discovery intent disappeared after commit")
            steps = await unit_of_work.workflows.list_steps(workflow.id)
            if len(steps) != 1 or steps[0].kind is not WorkflowStepKind.DISCOVERY_TOOL:
                raise WorkflowDiscoveryConflictError("persisted discovery shape is invalid")
            step = steps[0]
            if step.invocation_id is None:
                raise WorkflowDiscoveryConflictError("discovery invocation link is missing")
            invocation = await unit_of_work.tool_invocations.get(step.invocation_id)
            if invocation is None:
                raise WorkflowDiscoveryConflictError("discovery invocation is missing")
            await self._validate_persisted_intent(unit_of_work, command, workflow, invocation)

            if (
                workflow.status is WorkflowStatus.PLANNING
                and step.status is WorkflowStepStatus.SUCCEEDED
                and invocation.status is ToolInvocationStatus.SUCCEEDED
                and invocation.result is not None
            ):
                return RunWorkflowDiscoveryResult(
                    workflow_id=workflow.id,
                    step_id=step.id,
                    invocation_id=invocation.id,
                    output=invocation.result,
                    replayed=True,
                )
            if workflow.status is WorkflowStatus.DISCOVERY_FAILED:
                raise ToolDispatchError(invocation.failure_code or _ADAPTER_ERROR)
            if (
                workflow.status is WorkflowStatus.DISCOVERY_RUNNING
                or step.status is WorkflowStepStatus.RUNNING
                or invocation.status is ToolInvocationStatus.RUNNING
            ):
                raise WorkflowDiscoveryInProgressError(
                    "discovery already crossed the dispatch boundary"
                )
            if not (
                workflow.status is WorkflowStatus.DISCOVERY_PENDING
                and step.status is WorkflowStepStatus.PENDING
                and invocation.status is ToolInvocationStatus.PENDING
            ):
                raise WorkflowDiscoveryConflictError("persisted discovery states disagree")

            eligible = await self._load_eligible(unit_of_work, command, lock_state=True)
            adapter = self._adapter_resolver.resolve(eligible.version.manifest.adapter_key)
            if adapter is None:
                raise ToolDispatchError(_ADAPTER_UNAVAILABLE)
            running_workflow = workflow.start_discovery(at=started_at)
            running_step = step.start(at=started_at)
            running_invocation = invocation.start(at=started_at)
            await unit_of_work.workflows.update_lifecycle(
                previous=workflow,
                updated=running_workflow,
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=step,
                updated=running_step,
            )
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=invocation,
                updated=running_invocation,
            )
            await unit_of_work.turns.append_event(
                turn_id=command.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=command.attempt_id,
                kind=ExecutionEventKind.TOOL_STARTED,
                payload={
                    "invocation_id": str(invocation.id),
                    "tool_definition_id": str(invocation.tool_definition_id),
                    "tool_version_id": str(invocation.tool_version_id),
                },
                occurred_at=started_at,
            )
            await unit_of_work.commit()
        return _ClaimedDiscovery(
            workflow=running_workflow,
            step=running_step,
            invocation=running_invocation,
            eligible_tool=eligible,
            adapter=adapter,
        )

    async def _record_success(
        self,
        claimed: _ClaimedDiscovery,
        output: JsonObject,
    ) -> RunWorkflowDiscoveryResult:
        completed_at = self._clock.now()
        succeeded_invocation = claimed.invocation.succeed(at=completed_at, result=output)
        succeeded_step = claimed.step.succeed(at=completed_at)
        planning_workflow = claimed.workflow.begin_planning(at=completed_at)
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=claimed.invocation,
                updated=succeeded_invocation,
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=claimed.step,
                updated=succeeded_step,
            )
            await unit_of_work.workflows.update_lifecycle(
                previous=claimed.workflow,
                updated=planning_workflow,
            )
            await unit_of_work.turns.append_event(
                turn_id=claimed.invocation.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=claimed.invocation.attempt_id,
                kind=ExecutionEventKind.TOOL_COMPLETED,
                payload={"invocation_id": str(claimed.invocation.id)},
                occurred_at=completed_at,
            )
            await unit_of_work.commit()
        return RunWorkflowDiscoveryResult(
            workflow_id=claimed.workflow.id,
            step_id=claimed.step.id,
            invocation_id=claimed.invocation.id,
            output=output,
            replayed=False,
        )

    async def _record_failure(
        self,
        claimed: _ClaimedDiscovery,
        failure_code: str,
    ) -> None:
        failed_at = self._clock.now()
        failed_invocation = claimed.invocation.fail(
            at=failed_at,
            failure_code=failure_code,
        )
        failed_step = claimed.step.fail(at=failed_at, failure_code=failure_code)
        failed_workflow = claimed.workflow.fail_discovery(at=failed_at)
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(claimed.invocation.turn_id)
            attempt = await unit_of_work.turns.get_attempt(claimed.invocation.attempt_id)
            if (
                turn is None
                or attempt is None
                or turn.status is not TurnStatus.RUNNING
                or attempt.status is not TurnAttemptStatus.RUNNING
            ):
                raise WorkflowDiscoveryConflictError(
                    "running discovery lost its turn execution authority"
                )
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=claimed.invocation,
                updated=failed_invocation,
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=claimed.step,
                updated=failed_step,
            )
            await unit_of_work.workflows.update_lifecycle(
                previous=claimed.workflow,
                updated=failed_workflow,
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TOOL_FAILED,
                payload={
                    "invocation_id": str(claimed.invocation.id),
                    "failure_code": failure_code,
                },
                occurred_at=failed_at,
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.fail(at=failed_at),
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.fail(at=failed_at, failure_code=failure_code),
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_FAILED,
                payload={"failure_code": failure_code},
                occurred_at=failed_at,
            )
            await unit_of_work.commit()

    async def _validate_persisted_intent(
        self,
        unit_of_work: UnitOfWork,
        command: RunWorkflowDiscoveryCommand,
        workflow: TurnWorkflow,
        invocation: ToolInvocation,
    ) -> None:
        if (
            workflow.turn_id != command.turn_id
            or workflow.attempt_id != command.attempt_id
            or invocation.turn_id != command.turn_id
            or invocation.attempt_id != command.attempt_id
            or invocation.tool_version_id != command.tool_version_id
            or invocation.arguments != command.arguments
        ):
            raise WorkflowDiscoveryConflictError("command differs from persisted discovery")
        evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(invocation.id)
        if len(evaluations) != 1:
            raise WorkflowDiscoveryConflictError("discovery policy evidence is invalid")
        evaluation = evaluations[0]
        if (
            evaluation.team_id != command.team_id
            or evaluation.requester_actor_id != command.actor_id
            or evaluation.agent_version_id != command.agent_version_id
            or evaluation.environment is not command.environment
            or evaluation.granted_scopes != command.granted_scopes
        ):
            raise WorkflowDiscoveryConflictError("authority differs from persisted discovery")

    async def _validate_execution(
        self,
        unit_of_work: UnitOfWork,
        command: RunWorkflowDiscoveryCommand,
        turn: Turn | None,
        attempt: TurnAttempt | None,
    ) -> None:
        if turn is None:
            raise TurnNotFoundError(f"turn {command.turn_id} was not found")
        if attempt is None:
            raise TurnAttemptNotFoundError(f"turn attempt {command.attempt_id} was not found")
        if attempt.turn_id != turn.id:
            raise TurnAttemptMismatchError(
                f"turn attempt {command.attempt_id} does not belong to turn {command.turn_id}"
            )
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"conversation {turn.conversation_id} was not found")
        if conversation.team_id != command.team_id:
            raise TurnTeamMismatchError(f"turn {command.turn_id} was not found")
        if (
            turn.agent_version_id != command.agent_version_id
            or turn.status is not TurnStatus.RUNNING
            or attempt.status is not TurnAttemptStatus.RUNNING
        ):
            raise ToolDispatchError(_EXECUTION_NOT_RUNNING)

    async def _load_eligible(
        self,
        unit_of_work: UnitOfWork,
        command: RunWorkflowDiscoveryCommand,
        *,
        lock_state: bool = False,
    ) -> EligibleTool:
        if lock_state:
            state = await unit_of_work.tools.get_version_state_for_update(command.tool_version_id)
            if state is None or state.status is not ToolLifecycleStatus.ACTIVE:
                raise ToolDispatchError(_NOT_ELIGIBLE)
        eligible_tools = await unit_of_work.tools.list_eligible_for_agent(
            team_id=command.team_id,
            agent_version_id=command.agent_version_id,
        )
        eligible = next(
            (tool for tool in eligible_tools if tool.version.id == command.tool_version_id),
            None,
        )
        if eligible is None:
            raise ToolDispatchError(_NOT_ELIGIBLE)
        if eligible.version.manifest.effect is not ToolEffect.READ_ONLY:
            raise ToolDispatchError(_EFFECT_NOT_ALLOWED)
        if not set(eligible.version.manifest.required_scopes).issubset(command.granted_scopes):
            raise ToolDispatchError(_SCOPE_DENIED)
        if self._schema_validator.validate_instance(
            instance=command.arguments,
            schema=eligible.version.manifest.input_schema,
        ):
            raise ToolDispatchError(_ARGUMENTS_INVALID)
        return eligible

    @staticmethod
    def _policy_context(
        command: RunWorkflowDiscoveryCommand,
        eligible: EligibleTool,
    ) -> PolicyContext:
        return PolicyContext(
            team_id=command.team_id,
            actor_id=command.actor_id,
            agent_version_id=command.agent_version_id,
            tool_team_id=eligible.definition.team_id,
            tool_definition_id=eligible.definition.id,
            tool_version_id=eligible.version.id,
            effect=eligible.version.manifest.effect,
            required_scopes=eligible.version.manifest.required_scopes,
            granted_scopes=command.granted_scopes,
            environment=command.environment,
            arguments=command.arguments,
            is_bound=True,
            is_active=True,
            is_conformant=True,
        )
