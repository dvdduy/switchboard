"""Resume and execute one approved frozen workflow from PostgreSQL progress."""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from switchboard.application.errors import (
    ToolDispatchError,
    WorkflowExecutionConflictError,
    WorkflowExecutionInProgressError,
    WorkflowToolIneligibleError,
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
from switchboard.domain.approvals import ApprovalStatus, PolicyEvaluationRecord
from switchboard.domain.conversations import MessageRole
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ExecutionEventId,
    MessageId,
    TeamId,
    TurnWorkflowId,
)
from switchboard.domain.json_values import JsonObject, mutable_json_value
from switchboard.domain.policy import (
    PolicyContext,
    PolicyDecision,
    evaluate_policy,
    fingerprint_action,
    summarize_action,
)
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import EligibleTool, ToolEffect, ToolLifecycleStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflow_approvals import (
    WorkflowPlanActionSummary,
    WorkflowPlanApproval,
)
from switchboard.domain.workflows import (
    TurnWorkflow,
    WorkflowPlanAction,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepKind,
    WorkflowStepStatus,
    fingerprint_workflow_plan,
)

_ADAPTER_UNAVAILABLE = "tool_adapter_unavailable"
_ADAPTER_ERROR = "tool_adapter_error"
_OUTPUT_INVALID = "tool_output_invalid"
_TIMEOUT = "tool_timeout"


@dataclass(frozen=True, slots=True)
class RunApprovedWorkflowCommand:
    team_id: TeamId
    workflow_id: TurnWorkflowId
    recover_running_as_unknown: bool = False


@dataclass(frozen=True, slots=True)
class RunApprovedWorkflowResult:
    workflow_id: TurnWorkflowId
    output_message_id: MessageId
    response_text: str
    mutation_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class _ClaimedMutation:
    invocation: ToolInvocation
    step: WorkflowStep
    eligible: EligibleTool
    adapter: ToolAdapter


class RunApprovedWorkflow:
    """Select and commit one durable mutation at a time, then finalize atomically."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        adapter_resolver: ToolAdapterResolver,
        schema_validator: JsonSchemaValidator,
        clock: Clock,
        message_ids: IdGenerator[MessageId],
        event_ids: IdGenerator[ExecutionEventId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._clock = clock
        self._message_ids = message_ids
        self._event_ids = event_ids

    async def execute(
        self,
        command: RunApprovedWorkflowCommand,
    ) -> RunApprovedWorkflowResult:
        replay = await self._resume(command)
        if replay is not None:
            return replay
        if command.recover_running_as_unknown:
            await self._recover_interrupted_running(command)

        while True:
            claimed = await self._claim_next(command)
            if claimed is None:
                return await self._finalize(command)
            await self._dispatch_and_record(claimed)

    async def _resume(
        self,
        command: RunApprovedWorkflowCommand,
    ) -> RunApprovedWorkflowResult | None:
        at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            workflow = await unit_of_work.workflows.get(command.workflow_id)
            if workflow is None:
                raise WorkflowExecutionConflictError("workflow was not found")
            turn = await unit_of_work.turns.get_for_update(workflow.turn_id)
            workflow = await unit_of_work.workflows.get_for_turn_for_update(workflow.turn_id)
            if workflow is None or workflow.id != command.workflow_id:
                raise WorkflowExecutionConflictError("workflow authority changed")
            if workflow.status in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.REVIEW_REQUIRED,
            }:
                return await self._completed_result(
                    unit_of_work,
                    command,
                    workflow,
                    replayed=True,
                )
            if turn is None:
                raise WorkflowExecutionConflictError("workflow turn was not found")
            conversation = await unit_of_work.conversations.get(turn.conversation_id)
            if conversation is None or conversation.team_id != command.team_id:
                raise WorkflowExecutionConflictError("workflow was not found")
            attempt = await unit_of_work.turns.get_attempt(workflow.attempt_id)
            if attempt is None:
                raise WorkflowExecutionConflictError("workflow attempt was not found")

            approval = (
                None
                if workflow.approval_id is None
                else await unit_of_work.workflow_plan_approvals.get_for_update(workflow.approval_id)
            )
            await self._validate_frozen_plan(unit_of_work, workflow, approval)

            if workflow.status is WorkflowStatus.COMPLETING:
                if (
                    turn.status is not TurnStatus.RUNNING
                    or attempt.status is not TurnAttemptStatus.RUNNING
                ):
                    raise WorkflowExecutionConflictError("completing workflow authority is invalid")
                return None
            if workflow.status is WorkflowStatus.RUNNING:
                if (
                    approval is None
                    or approval.status is not ApprovalStatus.CONSUMED
                    or turn.status is not TurnStatus.RUNNING
                    or attempt.status is not TurnAttemptStatus.RUNNING
                ):
                    raise WorkflowExecutionConflictError("running workflow authority is invalid")
                return None
            if (
                workflow.status is not WorkflowStatus.AWAITING_CONFIRMATION
                or approval is None
                or approval.status is not ApprovalStatus.APPROVED
                or turn.status is not TurnStatus.AWAITING_CONFIRMATION
                or attempt.status is not TurnAttemptStatus.AWAITING_CONFIRMATION
            ):
                raise WorkflowExecutionConflictError("workflow is not approved for resume")
            if approval.is_expired(at=at):
                raise WorkflowExecutionConflictError("workflow approval is expired")

            consumed = approval.consume(at=at)
            resumed_workflow = workflow.resume(at=at)
            await unit_of_work.workflow_plan_approvals.update_lifecycle(
                previous=approval,
                updated=consumed,
            )
            await unit_of_work.workflows.update_lifecycle(
                previous=workflow,
                updated=resumed_workflow,
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=turn.resume(),
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.resume(),
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.WORKFLOW_RESUMED,
                payload={
                    "status": resumed_workflow.status.value,
                    "workflow_id": str(workflow.id),
                },
                occurred_at=at,
            )
            await unit_of_work.commit()
        return None

    async def _claim_next(
        self,
        command: RunApprovedWorkflowCommand,
    ) -> _ClaimedMutation | None:
        at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            initial = await unit_of_work.workflows.get(command.workflow_id)
            if initial is None:
                raise WorkflowExecutionConflictError("workflow was not found")
            turn = await unit_of_work.turns.get_for_update(initial.turn_id)
            workflow = await unit_of_work.workflows.get_for_turn_for_update(initial.turn_id)
            if workflow is None or workflow.id != command.workflow_id or turn is None:
                raise WorkflowExecutionConflictError("workflow authority changed")
            if workflow.status in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.REVIEW_REQUIRED,
            }:
                return None
            if workflow.status is WorkflowStatus.COMPLETING:
                return None
            if (
                workflow.status is not WorkflowStatus.RUNNING
                or turn.status is not TurnStatus.RUNNING
            ):
                raise WorkflowExecutionConflictError("workflow is not running")
            steps = await unit_of_work.workflows.list_steps(workflow.id)
            mutations = tuple(step for step in steps if step.kind is WorkflowStepKind.MUTATION_TOOL)
            stopped = False
            for step in mutations:
                if step.status is WorkflowStepStatus.SUCCEEDED:
                    if stopped:
                        raise WorkflowExecutionConflictError(
                            "workflow succeeded after a terminal mutation outcome"
                        )
                    continue
                if step.status is WorkflowStepStatus.RUNNING:
                    raise WorkflowExecutionInProgressError(
                        "another runner owns the mutation dispatch boundary"
                    )
                if step.status in {
                    WorkflowStepStatus.FAILED,
                    WorkflowStepStatus.UNKNOWN,
                }:
                    if stopped:
                        raise WorkflowExecutionConflictError(
                            "workflow has multiple terminal mutation outcomes"
                        )
                    stopped = True
                    continue
                if step.status is WorkflowStepStatus.SKIPPED:
                    if not stopped:
                        if step.failure_code != "tool_not_eligible":
                            raise WorkflowExecutionConflictError(
                                "workflow skipped a mutation before a terminal outcome"
                            )
                        stopped = True
                    continue
                if stopped:
                    raise WorkflowExecutionConflictError(
                        "workflow retained pending work after a terminal outcome"
                    )
                if step.status is not WorkflowStepStatus.PENDING:
                    raise WorkflowExecutionConflictError(
                        "workflow mutation requires failure handling"
                    )
                if step.invocation_id is None:
                    raise WorkflowExecutionConflictError("mutation invocation link is missing")
                invocation = await unit_of_work.tool_invocations.get(step.invocation_id)
                if (
                    invocation is None
                    or invocation.status is not ToolInvocationStatus.AWAITING_CONFIRMATION
                ):
                    raise WorkflowExecutionConflictError("mutation invocation state is invalid")
                try:
                    eligible = await self._revalidate_invocation(unit_of_work, invocation)
                except WorkflowToolIneligibleError:
                    await self._skip_undispatched_mutations(
                        unit_of_work,
                        step,
                        invocation,
                        at=at,
                        failure_code="tool_not_eligible",
                    )
                    await unit_of_work.commit()
                    return None
                adapter = self._adapter_resolver.resolve(eligible.version.manifest.adapter_key)
                if adapter is None:
                    raise ToolDispatchError(_ADAPTER_UNAVAILABLE)
                running_invocation = invocation.start(at=at)
                running_step = step.start(at=at)
                await unit_of_work.tool_invocations.update_lifecycle(
                    previous=invocation,
                    updated=running_invocation,
                )
                await unit_of_work.workflows.update_step_lifecycle(
                    previous=step,
                    updated=running_step,
                )
                await unit_of_work.turns.append_event(
                    turn_id=turn.id,
                    event_id=self._event_ids.new(),
                    attempt_id=workflow.attempt_id,
                    kind=ExecutionEventKind.TOOL_STARTED,
                    payload={
                        "invocation_id": str(invocation.id),
                        "tool_definition_id": str(invocation.tool_definition_id),
                        "tool_version_id": str(invocation.tool_version_id),
                    },
                    occurred_at=at,
                )
                await unit_of_work.commit()
                return _ClaimedMutation(
                    invocation=running_invocation,
                    step=running_step,
                    eligible=eligible,
                    adapter=adapter,
                )
            return None

    async def _dispatch_and_record(self, claimed: _ClaimedMutation) -> None:
        try:
            result = await asyncio.wait_for(
                claimed.adapter.invoke(
                    ToolInvocationRequest(
                        arguments=claimed.invocation.arguments,
                        idempotency_key=claimed.invocation.idempotency_key,
                    )
                ),
                timeout=claimed.eligible.version.manifest.timeout_ms / 1_000,
            )
        except TimeoutError:
            await self._record_unknown(claimed, _TIMEOUT)
            return
        except Exception:
            await self._record_unknown(claimed, _ADAPTER_ERROR)
            return

        if isinstance(result, ToolInvocationFailure):
            failure_code = f"tool.{result.error_code}"
            await self._record_declared_failure(claimed, failure_code)
            return
        if not isinstance(result, ToolInvocationSuccess):
            await self._record_unknown(claimed, _ADAPTER_ERROR)
            return
        output = mutable_json_value(result.output)
        if not isinstance(output, dict) or self._schema_validator.validate_instance(
            instance=output,
            schema=claimed.eligible.version.manifest.output_schema,
        ):
            await self._record_unknown(claimed, _OUTPUT_INVALID)
            return
        await self._record_success(claimed, output)

    async def _record_success(
        self,
        claimed: _ClaimedMutation,
        output: JsonObject,
    ) -> None:
        at = self._clock.now()
        succeeded_invocation = claimed.invocation.succeed(at=at, result=output)
        succeeded_step = claimed.step.succeed(at=at)
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=claimed.invocation,
                updated=succeeded_invocation,
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=claimed.step,
                updated=succeeded_step,
            )
            await unit_of_work.turns.append_event(
                turn_id=claimed.invocation.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=claimed.invocation.attempt_id,
                kind=ExecutionEventKind.TOOL_COMPLETED,
                payload={"invocation_id": str(claimed.invocation.id)},
                occurred_at=at,
            )
            await unit_of_work.commit()

    async def _record_declared_failure(
        self,
        claimed: _ClaimedMutation,
        failure_code: str,
    ) -> None:
        at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=claimed.invocation,
                updated=claimed.invocation.fail(at=at, failure_code=failure_code),
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=claimed.step,
                updated=claimed.step.fail(at=at, failure_code=failure_code),
            )
            await self._skip_later_mutations(
                unit_of_work,
                claimed.step,
                at=at,
                failure_code="stopped_after_failure",
            )
            await unit_of_work.turns.append_event(
                turn_id=claimed.invocation.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=claimed.invocation.attempt_id,
                kind=ExecutionEventKind.TOOL_FAILED,
                payload={
                    "failure_code": failure_code,
                    "invocation_id": str(claimed.invocation.id),
                },
                occurred_at=at,
            )
            await unit_of_work.commit()

    async def _record_unknown(
        self,
        claimed: _ClaimedMutation,
        failure_code: str,
    ) -> None:
        at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=claimed.invocation,
                updated=claimed.invocation.mark_unknown(
                    at=at,
                    failure_code=failure_code,
                ),
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=claimed.step,
                updated=claimed.step.mark_unknown(at=at, failure_code=failure_code),
            )
            await self._skip_later_mutations(
                unit_of_work,
                claimed.step,
                at=at,
                failure_code="stopped_after_unknown",
            )
            await unit_of_work.turns.append_event(
                turn_id=claimed.invocation.turn_id,
                event_id=self._event_ids.new(),
                attempt_id=claimed.invocation.attempt_id,
                kind=ExecutionEventKind.TOOL_FAILED,
                payload={
                    "failure_code": failure_code,
                    "invocation_id": str(claimed.invocation.id),
                    "outcome": "unknown",
                },
                occurred_at=at,
            )
            await unit_of_work.commit()

    async def _skip_later_mutations(
        self,
        unit_of_work: UnitOfWork,
        current: WorkflowStep,
        *,
        at: datetime,
        failure_code: str,
    ) -> None:
        steps = await unit_of_work.workflows.list_steps(current.workflow_id)
        for step in steps:
            if (
                step.kind is not WorkflowStepKind.MUTATION_TOOL
                or step.step_number <= current.step_number
            ):
                continue
            if step.status is not WorkflowStepStatus.PENDING or step.invocation_id is None:
                raise WorkflowExecutionConflictError("later mutation is not safely skippable")
            invocation = await unit_of_work.tool_invocations.get(step.invocation_id)
            if (
                invocation is None
                or invocation.status is not ToolInvocationStatus.AWAITING_CONFIRMATION
            ):
                raise WorkflowExecutionConflictError(
                    "later mutation invocation is not safely cancellable"
                )
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=invocation,
                updated=invocation.cancel(at=at),
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=step,
                updated=step.skip(at=at, failure_code=failure_code),
            )

    async def _skip_undispatched_mutations(
        self,
        unit_of_work: UnitOfWork,
        current_step: WorkflowStep,
        current_invocation: ToolInvocation,
        *,
        at: datetime,
        failure_code: str,
    ) -> None:
        await unit_of_work.tool_invocations.update_lifecycle(
            previous=current_invocation,
            updated=current_invocation.cancel(at=at),
        )
        await unit_of_work.workflows.update_step_lifecycle(
            previous=current_step,
            updated=current_step.skip(at=at, failure_code=failure_code),
        )
        await self._skip_later_mutations(
            unit_of_work,
            current_step,
            at=at,
            failure_code=failure_code,
        )

    async def _recover_interrupted_running(
        self,
        command: RunApprovedWorkflowCommand,
    ) -> None:
        at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            initial = await unit_of_work.workflows.get(command.workflow_id)
            if initial is None:
                raise WorkflowExecutionConflictError("workflow was not found")
            turn = await unit_of_work.turns.get_for_update(initial.turn_id)
            workflow = await unit_of_work.workflows.get_for_turn_for_update(initial.turn_id)
            if (
                workflow is None
                or workflow.id != command.workflow_id
                or turn is None
                or workflow.status is not WorkflowStatus.RUNNING
                or turn.status is not TurnStatus.RUNNING
            ):
                return
            steps = await unit_of_work.workflows.list_steps(workflow.id)
            running = tuple(
                step
                for step in steps
                if step.kind is WorkflowStepKind.MUTATION_TOOL
                and step.status is WorkflowStepStatus.RUNNING
            )
            if not running:
                return
            if len(running) != 1:
                raise WorkflowExecutionConflictError("interrupted mutation shape is invalid")
            step = running[0]
            invocation_id = step.invocation_id
            if invocation_id is None:
                raise WorkflowExecutionConflictError("interrupted mutation shape is invalid")
            invocation = await unit_of_work.tool_invocations.get(invocation_id)
            if invocation is None or invocation.status is not ToolInvocationStatus.RUNNING:
                raise WorkflowExecutionConflictError("interrupted invocation state is invalid")
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=invocation,
                updated=invocation.mark_unknown(
                    at=at,
                    failure_code="process_interrupted",
                ),
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=step,
                updated=step.mark_unknown(
                    at=at,
                    failure_code="process_interrupted",
                ),
            )
            await self._skip_later_mutations(
                unit_of_work,
                step,
                at=at,
                failure_code="stopped_after_unknown",
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=workflow.attempt_id,
                kind=ExecutionEventKind.TOOL_FAILED,
                payload={
                    "failure_code": "process_interrupted",
                    "invocation_id": str(invocation.id),
                    "outcome": "unknown",
                },
                occurred_at=at,
            )
            await unit_of_work.commit()

    async def _finalize(
        self,
        command: RunApprovedWorkflowCommand,
    ) -> RunApprovedWorkflowResult:
        at = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            initial = await unit_of_work.workflows.get(command.workflow_id)
            if initial is None:
                raise WorkflowExecutionConflictError("workflow was not found")
            turn = await unit_of_work.turns.get_for_update(initial.turn_id)
            workflow = await unit_of_work.workflows.get_for_turn_for_update(initial.turn_id)
            if workflow is None or workflow.id != command.workflow_id or turn is None:
                raise WorkflowExecutionConflictError("workflow authority changed")
            if workflow.status in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.REVIEW_REQUIRED,
            }:
                return await self._completed_result(
                    unit_of_work,
                    command,
                    workflow,
                    replayed=True,
                )
            attempt = await unit_of_work.turns.get_attempt(workflow.attempt_id)
            conversation = await unit_of_work.conversations.get(turn.conversation_id)
            if (
                attempt is None
                or conversation is None
                or conversation.team_id != command.team_id
                or turn.status is not TurnStatus.RUNNING
                or attempt.status is not TurnAttemptStatus.RUNNING
            ):
                raise WorkflowExecutionConflictError("finalization authority is invalid")
            steps = await unit_of_work.workflows.list_steps(workflow.id)
            mutations = tuple(step for step in steps if step.kind is WorkflowStepKind.MUTATION_TOOL)
            succeeded_count = sum(step.status is WorkflowStepStatus.SUCCEEDED for step in mutations)
            failed_count = sum(step.status is WorkflowStepStatus.FAILED for step in mutations)
            unknown_count = sum(step.status is WorkflowStepStatus.UNKNOWN for step in mutations)
            skipped_count = sum(step.status is WorkflowStepStatus.SKIPPED for step in mutations)
            if succeeded_count + failed_count + unknown_count + skipped_count != len(mutations):
                raise WorkflowExecutionConflictError("workflow mutations are not terminal")
            if unknown_count == 1 and failed_count == 0:
                terminal_status = WorkflowStatus.REVIEW_REQUIRED
                failure_code = "workflow_outcome_unknown"
            elif failed_count == 1 and unknown_count == 0:
                terminal_status = WorkflowStatus.FAILED
                failure_code = "workflow_mutation_failed"
            elif failed_count == 0 and unknown_count == 0 and skipped_count == 0:
                terminal_status = WorkflowStatus.COMPLETED
                failure_code = None
            elif failed_count == 0 and unknown_count == 0 and skipped_count > 0:
                terminal_status = WorkflowStatus.FAILED
                failure_code = "workflow_precondition_failed"
            else:
                raise WorkflowExecutionConflictError("workflow terminal outcome is invalid")
            final_steps = tuple(
                step for step in steps if step.kind is WorkflowStepKind.FINAL_RESPONSE
            )
            if len(final_steps) != 1 or final_steps[0].status is not WorkflowStepStatus.PENDING:
                raise WorkflowExecutionConflictError("final response step is invalid")
            final_step = final_steps[0]
            if workflow.status is WorkflowStatus.RUNNING:
                completing = workflow.begin_completion(at=at)
                await unit_of_work.workflows.update_lifecycle(
                    previous=workflow,
                    updated=completing,
                )
            elif workflow.status is WorkflowStatus.COMPLETING:
                completing = workflow
            else:
                raise WorkflowExecutionConflictError("workflow cannot be finalized")

            response_text = self._summary_text(
                succeeded=succeeded_count,
                failed=failed_count,
                unknown=unknown_count,
                skipped=skipped_count,
            )
            message = await unit_of_work.conversations.append_message(
                conversation_id=conversation.id,
                message_id=self._message_ids.new(),
                role=MessageRole.ASSISTANT,
                content=response_text,
                created_at=at,
            )
            running_final = final_step.start(at=at)
            succeeded_final = running_final.succeed(at=at, output_message_id=message.id)
            await unit_of_work.workflows.update_step_lifecycle(
                previous=final_step,
                updated=running_final,
            )
            await unit_of_work.workflows.update_step_lifecycle(
                previous=running_final,
                updated=succeeded_final,
            )
            if terminal_status is WorkflowStatus.COMPLETED:
                completed = completing.complete(output_message_id=message.id, at=at)
            elif terminal_status is WorkflowStatus.FAILED:
                completed = completing.fail(output_message_id=message.id, at=at)
            else:
                completed = completing.require_review(output_message_id=message.id, at=at)
            await unit_of_work.workflows.update_lifecycle(
                previous=completing,
                updated=completed,
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.WORKFLOW_TERMINAL,
                payload={
                    "mutation_count": len(mutations),
                    "status": completed.status.value,
                    "workflow_id": str(workflow.id),
                },
                occurred_at=at,
            )
            if failure_code is None:
                updated_attempt = attempt.succeed(at=at)
                updated_turn = turn.complete(at=at)
                event_kind = ExecutionEventKind.TURN_COMPLETED
                event_payload: dict[str, object] = {
                    "mutation_count": len(mutations),
                    "workflow_id": str(workflow.id),
                }
            else:
                updated_attempt = attempt.fail(at=at, failure_code=failure_code)
                updated_turn = turn.fail(at=at)
                event_kind = ExecutionEventKind.TURN_FAILED
                event_payload = {
                    "failure_code": failure_code,
                    "workflow_id": str(workflow.id),
                }
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=updated_attempt,
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn,
                updated=updated_turn,
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=event_kind,
                payload=event_payload,
                occurred_at=at,
            )
            await unit_of_work.commit()
            return RunApprovedWorkflowResult(
                workflow_id=workflow.id,
                output_message_id=message.id,
                response_text=response_text,
                mutation_count=len(mutations),
                replayed=False,
            )

    async def _completed_result(
        self,
        unit_of_work: UnitOfWork,
        command: RunApprovedWorkflowCommand,
        workflow: TurnWorkflow,
        *,
        replayed: bool,
    ) -> RunApprovedWorkflowResult:
        if workflow.output_message_id is None:
            raise WorkflowExecutionConflictError("completed workflow output is missing")
        turn = await unit_of_work.turns.get(workflow.turn_id)
        if turn is None:
            raise WorkflowExecutionConflictError("completed workflow turn is missing")
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
        if conversation is None or conversation.team_id != command.team_id:
            raise WorkflowExecutionConflictError("workflow was not found")
        message = await unit_of_work.conversations.get_message(
            conversation_id=conversation.id,
            message_id=workflow.output_message_id,
        )
        if message is None:
            raise WorkflowExecutionConflictError("completed workflow message is missing")
        steps = await unit_of_work.workflows.list_steps(workflow.id)
        mutation_count = sum(step.kind is WorkflowStepKind.MUTATION_TOOL for step in steps)
        return RunApprovedWorkflowResult(
            workflow_id=workflow.id,
            output_message_id=message.id,
            response_text=message.content,
            mutation_count=mutation_count,
            replayed=replayed,
        )

    async def _validate_frozen_plan(
        self,
        unit_of_work: UnitOfWork,
        workflow: TurnWorkflow,
        approval: WorkflowPlanApproval | None,
    ) -> None:
        steps = await unit_of_work.workflows.list_steps(workflow.id)
        if (
            len(steps) < 2
            or tuple(step.step_number for step in steps) != tuple(range(1, len(steps) + 1))
            or steps[0].kind is not WorkflowStepKind.DISCOVERY_TOOL
            or steps[0].status is not WorkflowStepStatus.SUCCEEDED
            or steps[-1].kind is not WorkflowStepKind.FINAL_RESPONSE
            or any(step.kind is not WorkflowStepKind.MUTATION_TOOL for step in steps[1:-1])
        ):
            raise WorkflowExecutionConflictError("frozen workflow step shape is invalid")
        discovery_id = steps[0].invocation_id
        if discovery_id is None:
            raise WorkflowExecutionConflictError("discovery evidence is missing")
        discovery_evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(
            discovery_id
        )
        if len(discovery_evaluations) != 1:
            raise WorkflowExecutionConflictError("discovery authority is invalid")
        authority = discovery_evaluations[0]
        actions: list[WorkflowPlanAction] = []
        safe_actions: list[WorkflowPlanActionSummary] = []
        for step in steps[1:-1]:
            if step.invocation_id is None:
                raise WorkflowExecutionConflictError("mutation evidence is missing")
            invocation = await unit_of_work.tool_invocations.get(step.invocation_id)
            if invocation is None:
                raise WorkflowExecutionConflictError("mutation invocation is missing")
            eligible, evaluation, context = await self._validated_action(
                unit_of_work,
                invocation,
            )
            del eligible
            if (
                evaluation.team_id != authority.team_id
                or evaluation.requester_actor_id != authority.requester_actor_id
                or evaluation.agent_version_id != authority.agent_version_id
                or evaluation.environment is not authority.environment
                or evaluation.turn_id != workflow.turn_id
                or evaluation.attempt_id != workflow.attempt_id
            ):
                raise WorkflowExecutionConflictError("mutation authority changed")
            actions.append(
                WorkflowPlanAction(
                    step_number=step.step_number,
                    invocation_id=invocation.id,
                    fingerprint=evaluation.fingerprint,
                )
            )
            safe_actions.append(
                WorkflowPlanActionSummary(
                    step_number=step.step_number,
                    action=summarize_action(context),
                )
            )
        expected = fingerprint_workflow_plan(
            team_id=authority.team_id,
            requester_actor_id=authority.requester_actor_id,
            agent_version_id=authority.agent_version_id,
            workflow_id=workflow.id,
            plan_version=workflow.plan_version,
            environment=authority.environment,
            policy_version=authority.evaluation.policy_version,
            actions=tuple(actions),
        )
        if workflow.plan_fingerprint != expected:
            raise WorkflowExecutionConflictError("workflow plan fingerprint changed")
        if actions:
            if (
                approval is None
                or approval.workflow_id != workflow.id
                or approval.team_id != authority.team_id
                or approval.requester_actor_id != authority.requester_actor_id
                or approval.fingerprint != expected
                or approval.safe_actions != tuple(safe_actions)
            ):
                raise WorkflowExecutionConflictError("workflow approval does not match plan")
        elif approval is not None or workflow.approval_id is not None:
            raise WorkflowExecutionConflictError("empty workflow plan must not have approval")

    async def _revalidate_invocation(
        self,
        unit_of_work: UnitOfWork,
        invocation: ToolInvocation,
    ) -> EligibleTool:
        eligible, _, context = await self._validated_action(unit_of_work, invocation)
        if evaluate_policy(context).decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            raise WorkflowToolIneligibleError("mutation tool is not eligible")
        return eligible

    async def _validated_action(
        self,
        unit_of_work: UnitOfWork,
        invocation: ToolInvocation,
    ) -> tuple[EligibleTool, PolicyEvaluationRecord, PolicyContext]:
        evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(invocation.id)
        if len(evaluations) != 1:
            raise WorkflowExecutionConflictError("mutation policy evidence is invalid")
        evaluation = evaluations[0]
        definition = await unit_of_work.tools.get_definition(invocation.tool_definition_id)
        version = await unit_of_work.tools.get_version(invocation.tool_version_id)
        bindings = await unit_of_work.tools.list_bindings(evaluation.agent_version_id)
        is_bound = any(
            binding.tool_definition_id == invocation.tool_definition_id
            and binding.tool_version_id == invocation.tool_version_id
            for binding in bindings
        )
        state = await unit_of_work.tools.get_version_state_for_update(invocation.tool_version_id)
        if definition is None or version is None or state is None:
            raise WorkflowExecutionConflictError("mutation tool evidence is missing")
        eligible = EligibleTool(definition=definition, version=version)
        context = PolicyContext(
            team_id=evaluation.team_id,
            actor_id=evaluation.requester_actor_id,
            agent_version_id=evaluation.agent_version_id,
            tool_team_id=eligible.definition.team_id,
            tool_definition_id=eligible.definition.id,
            tool_version_id=eligible.version.id,
            effect=eligible.version.manifest.effect,
            required_scopes=eligible.version.manifest.required_scopes,
            granted_scopes=evaluation.granted_scopes,
            environment=evaluation.environment,
            arguments=invocation.arguments,
            is_bound=is_bound,
            is_active=state.status is ToolLifecycleStatus.ACTIVE,
            is_conformant=state.activated_conformance_run_id is not None,
        )
        if (
            eligible.version.manifest.effect is not ToolEffect.MUTATING
            or not is_bound
            or state.activated_conformance_run_id is None
            or evaluation.invocation_id != invocation.id
            or evaluation.tool_definition_id != invocation.tool_definition_id
            or evaluation.tool_version_id != invocation.tool_version_id
            or evaluation.effect is not ToolEffect.MUTATING
            or evaluation.required_scopes != invocation.authorized_scopes
            or evaluation.evaluation.decision is not PolicyDecision.REQUIRE_CONFIRMATION
            or fingerprint_action(context) != evaluation.fingerprint
        ):
            raise WorkflowExecutionConflictError("mutation policy evidence changed")
        return eligible, evaluation, context

    @staticmethod
    def _summary_text(
        *,
        succeeded: int,
        failed: int,
        unknown: int,
        skipped: int,
    ) -> str:
        if unknown:
            return (
                "Workflow requires review: "
                f"{succeeded} succeeded, {unknown} outcome unknown, {skipped} skipped."
            )
        if failed:
            return f"Workflow stopped: {succeeded} succeeded, {failed} failed, {skipped} skipped."
        if skipped:
            return f"Workflow stopped before dispatch: {succeeded} succeeded, {skipped} skipped."
        noun = "update" if succeeded == 1 else "updates"
        return f"Workflow completed: {succeeded} planned {noun} succeeded."
