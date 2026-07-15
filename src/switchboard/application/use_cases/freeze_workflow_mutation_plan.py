"""Derive, validate, fingerprint, and atomically freeze one mutation plan."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from switchboard.application.errors import (
    ConversationNotFoundError,
    ToolDispatchError,
    TurnAttemptMismatchError,
    TurnAttemptNotFoundError,
    TurnNotFoundError,
    TurnTeamMismatchError,
    WorkflowPlanningConflictError,
    WorkflowPlanValidationError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from switchboard.domain.approvals import ApprovalStatus, PolicyEvaluationRecord
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
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.policy import (
    POLICY_VERSION,
    PolicyContext,
    PolicyDecision,
    PolicyEnvironment,
    evaluate_policy,
    fingerprint_action,
    summarize_action,
)
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import EligibleTool, ToolEffect, ToolLifecycleStatus
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus
from switchboard.domain.workflow_approvals import (
    WorkflowPlanActionSummary,
    WorkflowPlanApproval,
)
from switchboard.domain.workflows import (
    WorkflowPlanAction,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepKind,
    WorkflowStepStatus,
    fingerprint_workflow_plan,
)

MAX_WORKFLOW_MUTATIONS = 10
DEFAULT_WORKFLOW_APPROVAL_TTL = timedelta(minutes=15)
_NOT_ELIGIBLE = "tool_not_eligible"
_EFFECT_NOT_ALLOWED = "tool_effect_not_allowed"
_SCOPE_DENIED = "tool_scope_denied"
_ARGUMENTS_INVALID = "tool_arguments_invalid"
_POLICY_DENIED = "tool_policy_denied"


@dataclass(frozen=True, slots=True)
class FreezeWorkflowMutationPlanCommand:
    """Trusted reference-plan inputs for one completed discovery workflow."""

    team_id: TeamId
    actor_id: ActorId
    agent_version_id: AgentVersionId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    mutation_tool_version_id: ToolVersionId
    target_due_date: str
    granted_scopes: tuple[str, ...]
    environment: PolicyEnvironment = PolicyEnvironment.DEVELOPMENT

    def __post_init__(self) -> None:
        target = self.target_due_date.strip()
        if not target:
            raise WorkflowPlanValidationError("target due date must not be blank")
        object.__setattr__(self, "target_due_date", target)
        object.__setattr__(self, "granted_scopes", tuple(sorted(set(self.granted_scopes))))


@dataclass(frozen=True, slots=True)
class FreezeWorkflowMutationPlanResult:
    """Committed frozen-plan identity and durable pause result."""

    workflow_id: TurnWorkflowId
    approval_id: WorkflowPlanApprovalId | None
    mutation_count: int
    awaiting_confirmation: bool


class FreezeWorkflowMutationPlan:
    """Materialize only platform-derived sequential mutations from committed output."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        schema_validator: JsonSchemaValidator,
        clock: Clock,
        invocation_ids: IdGenerator[ToolInvocationId],
        policy_evaluation_ids: IdGenerator[PolicyEvaluationId],
        approval_ids: IdGenerator[WorkflowPlanApprovalId],
        step_ids: IdGenerator[WorkflowStepId],
        event_ids: IdGenerator[ExecutionEventId],
        approval_ttl: timedelta = DEFAULT_WORKFLOW_APPROVAL_TTL,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._schema_validator = schema_validator
        self._clock = clock
        self._invocation_ids = invocation_ids
        self._policy_evaluation_ids = policy_evaluation_ids
        self._approval_ids = approval_ids
        self._step_ids = step_ids
        self._event_ids = event_ids
        self._approval_ttl = approval_ttl

    async def execute(
        self,
        command: FreezeWorkflowMutationPlanCommand,
    ) -> FreezeWorkflowMutationPlanResult:
        planned_at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get_for_update(command.turn_id)
            attempt = await unit_of_work.turns.get_attempt(command.attempt_id)
            await self._validate_execution(unit_of_work, command, turn, attempt)
            if turn is None or attempt is None:
                raise RuntimeError("validated workflow execution disappeared")

            workflow = await unit_of_work.workflows.get_for_turn_for_update(command.turn_id)
            if workflow is None or workflow.attempt_id != command.attempt_id:
                raise WorkflowPlanningConflictError("workflow planning authority is missing")
            if workflow.status is not WorkflowStatus.PLANNING:
                raise WorkflowPlanningConflictError("workflow plan is already frozen")

            steps = await unit_of_work.workflows.list_steps(workflow.id)
            discovery = self._validated_discovery_step(steps)
            if discovery.invocation_id is None:
                raise WorkflowPlanValidationError("discovery invocation link is missing")
            discovery_invocation = await unit_of_work.tool_invocations.get(discovery.invocation_id)
            if (
                discovery_invocation is None
                or discovery_invocation.status is not ToolInvocationStatus.SUCCEEDED
                or discovery_invocation.result is None
            ):
                raise WorkflowPlanValidationError("discovery result is not committed")
            await self._validate_discovery_authority(
                unit_of_work,
                command,
                discovery_invocation.id,
            )
            targets = self._extract_targets(discovery_invocation.result)

            eligible = await self._load_mutation_tool(unit_of_work, command)
            existing_invocations = await unit_of_work.tool_invocations.list_for_turn(
                command.turn_id
            )
            next_invocation_number = existing_invocations[-1].invocation_number + 1
            predecessor = discovery
            actions: list[WorkflowPlanAction] = []
            safe_actions: list[WorkflowPlanActionSummary] = []

            for offset, target in enumerate(targets):
                arguments = {
                    "work_item_id": target,
                    "due_date": command.target_due_date,
                }
                if self._schema_validator.validate_instance(
                    instance=arguments,
                    schema=eligible.version.manifest.input_schema,
                ):
                    raise ToolDispatchError(_ARGUMENTS_INVALID)
                context = self._policy_context(command, eligible, arguments)
                evaluation = evaluate_policy(context)
                if evaluation.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
                    raise ToolDispatchError(_POLICY_DENIED)
                action_fingerprint = fingerprint_action(context)
                invocation_id = self._invocation_ids.new()
                step_number = offset + 2
                invocation = ToolInvocation(
                    id=invocation_id,
                    turn_id=command.turn_id,
                    attempt_id=command.attempt_id,
                    invocation_number=next_invocation_number + offset,
                    tool_definition_id=eligible.definition.id,
                    tool_version_id=eligible.version.id,
                    arguments=arguments,
                    idempotency_key=f"invocation:{invocation_id}",
                    authorized_scopes=eligible.version.manifest.required_scopes,
                    status=ToolInvocationStatus.AWAITING_CONFIRMATION,
                    created_at=planned_at,
                )
                step = WorkflowStep(
                    id=self._step_ids.new(),
                    workflow_id=workflow.id,
                    turn_id=workflow.turn_id,
                    attempt_id=workflow.attempt_id,
                    step_number=step_number,
                    kind=WorkflowStepKind.MUTATION_TOOL,
                    status=WorkflowStepStatus.PENDING,
                    predecessor_step_id=predecessor.id,
                    predecessor_step_number=predecessor.step_number,
                    invocation_id=invocation.id,
                    created_at=planned_at,
                )
                record = PolicyEvaluationRecord(
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
                    evaluation=evaluation,
                    fingerprint=action_fingerprint,
                    evaluated_at=planned_at,
                )
                await unit_of_work.tool_invocations.add(invocation)
                await unit_of_work.approvals.add_evaluation(record)
                await unit_of_work.workflows.add_step(step)
                actions.append(
                    WorkflowPlanAction(
                        step_number=step_number,
                        invocation_id=invocation.id,
                        fingerprint=action_fingerprint,
                    )
                )
                safe_actions.append(
                    WorkflowPlanActionSummary(
                        step_number=step_number,
                        action=summarize_action(context),
                    )
                )
                predecessor = step

            final_step = WorkflowStep(
                id=self._step_ids.new(),
                workflow_id=workflow.id,
                turn_id=workflow.turn_id,
                attempt_id=workflow.attempt_id,
                step_number=len(targets) + 2,
                kind=WorkflowStepKind.FINAL_RESPONSE,
                status=WorkflowStepStatus.PENDING,
                predecessor_step_id=predecessor.id,
                predecessor_step_number=predecessor.step_number,
                created_at=planned_at,
            )
            await unit_of_work.workflows.add_step(final_step)
            plan_fingerprint = fingerprint_workflow_plan(
                team_id=command.team_id,
                requester_actor_id=command.actor_id,
                agent_version_id=command.agent_version_id,
                workflow_id=workflow.id,
                plan_version=workflow.plan_version,
                environment=command.environment,
                policy_version=POLICY_VERSION,
                actions=tuple(actions),
            )

            if not targets:
                completing = workflow.begin_completion_without_mutations(
                    fingerprint=plan_fingerprint,
                    at=planned_at,
                )
                await unit_of_work.workflows.update_lifecycle(
                    previous=workflow,
                    updated=completing,
                )
                await unit_of_work.turns.append_event(
                    turn_id=turn.id,
                    event_id=self._event_ids.new(),
                    attempt_id=attempt.id,
                    kind=ExecutionEventKind.WORKFLOW_PLANNED,
                    payload={
                        "mutation_count": 0,
                        "status": completing.status.value,
                        "workflow_id": str(workflow.id),
                    },
                    occurred_at=planned_at,
                )
                await unit_of_work.commit()
                return FreezeWorkflowMutationPlanResult(
                    workflow_id=workflow.id,
                    approval_id=None,
                    mutation_count=0,
                    awaiting_confirmation=False,
                )

            approval = WorkflowPlanApproval(
                id=self._approval_ids.new(),
                workflow_id=workflow.id,
                team_id=command.team_id,
                requester_actor_id=command.actor_id,
                fingerprint=plan_fingerprint,
                safe_actions=tuple(safe_actions),
                status=ApprovalStatus.PENDING,
                created_at=planned_at,
                expires_at=planned_at + self._approval_ttl,
            )
            await unit_of_work.workflow_plan_approvals.add(approval)
            paused_workflow = workflow.await_confirmation(
                fingerprint=plan_fingerprint,
                approval_id=approval.id,
                at=planned_at,
            )
            await unit_of_work.workflows.update_lifecycle(
                previous=workflow,
                updated=paused_workflow,
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.WORKFLOW_PLANNED,
                payload={
                    "mutation_count": len(targets),
                    "status": paused_workflow.status.value,
                    "workflow_id": str(workflow.id),
                },
                occurred_at=planned_at,
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.await_confirmation(),
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.await_confirmation(),
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.APPROVAL_REQUIRED,
                payload={
                    "approval_id": str(approval.id),
                    "expires_at": approval.expires_at.isoformat(),
                    "fingerprint_version": approval.fingerprint.version,
                    "mutation_count": len(targets),
                    "safe_actions": [
                        {
                            "argument_fields": list(summary.action.argument_fields),
                            "effect": summary.action.effect.value,
                            "step_number": summary.step_number,
                            "tool_definition_id": str(summary.action.tool_definition_id),
                            "tool_version_id": str(summary.action.tool_version_id),
                        }
                        for summary in approval.safe_actions
                    ],
                    "workflow_id": str(workflow.id),
                },
                occurred_at=planned_at,
            )
            await unit_of_work.commit()
            return FreezeWorkflowMutationPlanResult(
                workflow_id=workflow.id,
                approval_id=approval.id,
                mutation_count=len(targets),
                awaiting_confirmation=True,
            )

    @staticmethod
    def _validated_discovery_step(steps: tuple[WorkflowStep, ...]) -> WorkflowStep:
        if (
            len(steps) != 1
            or steps[0].kind is not WorkflowStepKind.DISCOVERY_TOOL
            or steps[0].status is not WorkflowStepStatus.SUCCEEDED
            or steps[0].step_number != 1
        ):
            raise WorkflowPlanValidationError("workflow has an untrusted pre-freeze step shape")
        return steps[0]

    @staticmethod
    def _extract_targets(result: Mapping[str, object]) -> tuple[str, ...]:
        raw_items = result.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise WorkflowPlanValidationError("discovery result items are invalid")
        if len(raw_items) > MAX_WORKFLOW_MUTATIONS:
            raise WorkflowPlanValidationError("discovery result exceeds mutation bound")
        targets: list[str] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise WorkflowPlanValidationError("discovery result item is invalid")
            raw_id = raw_item.get("id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise WorkflowPlanValidationError("discovery result item id is invalid")
            targets.append(raw_id.strip())
        if len(targets) != len(set(targets)):
            raise WorkflowPlanValidationError("discovery result contains duplicate targets")
        return tuple(targets)

    async def _load_mutation_tool(
        self,
        unit_of_work: UnitOfWork,
        command: FreezeWorkflowMutationPlanCommand,
    ) -> EligibleTool:
        state = await unit_of_work.tools.get_version_state_for_update(
            command.mutation_tool_version_id
        )
        if state is None or state.status is not ToolLifecycleStatus.ACTIVE:
            raise ToolDispatchError(_NOT_ELIGIBLE)
        tools = await unit_of_work.tools.list_eligible_for_agent(
            team_id=command.team_id,
            agent_version_id=command.agent_version_id,
        )
        eligible = next(
            (tool for tool in tools if tool.version.id == command.mutation_tool_version_id),
            None,
        )
        if eligible is None:
            raise ToolDispatchError(_NOT_ELIGIBLE)
        if eligible.version.manifest.effect is not ToolEffect.MUTATING:
            raise ToolDispatchError(_EFFECT_NOT_ALLOWED)
        if not set(eligible.version.manifest.required_scopes).issubset(command.granted_scopes):
            raise ToolDispatchError(_SCOPE_DENIED)
        return eligible

    async def _validate_discovery_authority(
        self,
        unit_of_work: UnitOfWork,
        command: FreezeWorkflowMutationPlanCommand,
        invocation_id: ToolInvocationId,
    ) -> None:
        evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(invocation_id)
        if len(evaluations) != 1:
            raise WorkflowPlanValidationError("discovery policy evidence is invalid")
        evidence = evaluations[0]
        if (
            evidence.team_id != command.team_id
            or evidence.requester_actor_id != command.actor_id
            or evidence.agent_version_id != command.agent_version_id
            or evidence.environment is not command.environment
        ):
            raise WorkflowPlanValidationError("planning authority differs from discovery")

    @staticmethod
    def _policy_context(
        command: FreezeWorkflowMutationPlanCommand,
        eligible: EligibleTool,
        arguments: Mapping[str, object],
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
            arguments=arguments,
            is_bound=True,
            is_active=True,
            is_conformant=True,
        )

    @staticmethod
    async def _validate_execution(
        unit_of_work: UnitOfWork,
        command: FreezeWorkflowMutationPlanCommand,
        turn: Turn | None,
        attempt: TurnAttempt | None,
    ) -> None:
        if turn is None:
            raise TurnNotFoundError(f"turn {command.turn_id} was not found")
        if attempt is None:
            raise TurnAttemptNotFoundError(f"turn attempt {command.attempt_id} was not found")
        if attempt.turn_id != turn.id:
            raise TurnAttemptMismatchError("turn attempt does not belong to turn")
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
            raise WorkflowPlanningConflictError("turn is not running for planning")
