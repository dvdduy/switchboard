"""Safe read, decision, and resume workflows for durable approvals."""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from switchboard.application.errors import (
    ApprovalDecisionConflictError,
    ApprovalNotFoundError,
    ApprovalRevalidationError,
    ApprovalTeamMismatchError,
    IdempotencyConflictError,
    ToolDispatchError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.tool_adapter import (
    ToolAdapterResolver,
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
)
from switchboard.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from switchboard.application.services.command_idempotency import (
    fingerprint_approval_decision,
    hash_idempotency_key,
)
from switchboard.domain.approvals import ApprovalRequest, ApprovalStatus
from switchboard.domain.command_receipts import (
    ApprovalDecision,
    CommandOperation,
    CommandReceipt,
)
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    ApprovalRequestId,
    CommandReceiptId,
    ExecutionEventId,
    TeamId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
)
from switchboard.domain.json_values import JsonObject, mutable_json_value
from switchboard.domain.policy import (
    PolicyContext,
    PolicyDecision,
    evaluate_policy,
    fingerprint_action,
)
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import EligibleTool, ToolEffect, ToolLifecycleStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus

_REVALIDATION_FAILED = "approval_revalidation_failed"
_ADAPTER_UNAVAILABLE = "tool_adapter_unavailable"
_TIMEOUT = "tool_timeout"
_ADAPTER_ERROR = "tool_adapter_error"
_OUTPUT_INVALID = "tool_output_invalid"


@dataclass(frozen=True, slots=True)
class ApprovalReadModel:
    """Public-safe approval details with no arguments or fingerprint digest."""

    approval_id: ApprovalRequestId
    invocation_id: ToolInvocationId
    requester_actor_id: ActorId
    status: ApprovalStatus
    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    effect: ToolEffect
    argument_fields: tuple[str, ...]
    fingerprint_version: str
    created_at: datetime
    expires_at: datetime
    resolved_by_actor_id: ActorId | None
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class DecideApprovalCommand:
    team_id: TeamId
    actor_id: ActorId
    approval_id: ApprovalRequestId
    decision: ApprovalDecision
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DecideApprovalResult:
    approval: ApprovalReadModel
    invocation_status: ToolInvocationStatus


class ManageApprovals:
    """Apply one human decision and cross the mutation dispatch boundary safely."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        adapter_resolver: ToolAdapterResolver,
        schema_validator: JsonSchemaValidator,
        clock: Clock,
        receipt_ids: IdGenerator[CommandReceiptId],
        event_ids: IdGenerator[ExecutionEventId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._clock = clock
        self._receipt_ids = receipt_ids
        self._event_ids = event_ids

    async def get(self, *, team_id: TeamId, approval_id: ApprovalRequestId) -> ApprovalReadModel:
        approval = await self._load_for_team(team_id=team_id, approval_id=approval_id)
        if approval.status in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        } and approval.is_expired(at=self._clock.now()):
            approval = await self._expire_and_cancel(approval_id=approval.id, team_id=team_id)
        return _read_model(approval)

    async def decide(self, command: DecideApprovalCommand) -> DecideApprovalResult:
        request_fingerprint = fingerprint_approval_decision(
            team_id=command.team_id,
            approval_id=command.approval_id,
            actor_id=command.actor_id,
            decision=command.decision,
        )
        receipt = CommandReceipt(
            id=self._receipt_ids.new(),
            team_id=command.team_id,
            operation=CommandOperation.DECIDE_APPROVAL,
            command_scope=str(command.approval_id),
            idempotency_key_hash=hash_idempotency_key(command.idempotency_key),
            request_fingerprint=request_fingerprint,
            created_at=self._clock.now(),
            approval_id=command.approval_id,
            actor_id=command.actor_id,
            approval_decision=command.decision,
        )

        async with self._unit_of_work_factory() as unit_of_work:
            approval = await unit_of_work.approvals.get_request_for_update(command.approval_id)
            if approval is None:
                raise ApprovalNotFoundError("approval was not found")
            if approval.team_id != command.team_id:
                raise ApprovalTeamMismatchError("approval belongs to another team")
            authority, created = await unit_of_work.command_receipts.add_or_get(receipt)
            if not authority.has_same_request(request_fingerprint):
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )

            now = self._clock.now()
            if not created:
                await unit_of_work.commit()
            elif approval.is_expired(at=now):
                approval = await self._cancel_locked(
                    unit_of_work=unit_of_work,
                    approval=approval.expire(at=now),
                    previous_approval=approval,
                    decision_actor_id=None,
                    decision="expired",
                    at=now,
                )
                await unit_of_work.commit()
            elif approval.status is not ApprovalStatus.PENDING:
                raise ApprovalDecisionConflictError(
                    f"approval cannot be decided from {approval.status.value}"
                )
            elif command.decision is ApprovalDecision.REJECT:
                rejected = approval.reject(actor_id=command.actor_id, at=now)
                approval = await self._cancel_locked(
                    unit_of_work=unit_of_work,
                    approval=rejected,
                    previous_approval=approval,
                    decision_actor_id=command.actor_id,
                    decision="rejected",
                    at=now,
                )
                await unit_of_work.commit()
            else:
                approved = approval.approve(actor_id=command.actor_id, at=now)
                await unit_of_work.approvals.update_lifecycle(
                    previous=approval,
                    updated=approved,
                )
                await self._append_resolved(
                    unit_of_work=unit_of_work,
                    approval=approved,
                    invocation=await self._require_invocation_in(unit_of_work, approved),
                    decision="approved",
                    actor_id=command.actor_id,
                    at=now,
                )
                await unit_of_work.commit()
                approval = approved

        if command.decision is ApprovalDecision.APPROVE and approval.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.CONSUMED,
        }:
            approval, invocation = await self._resume_and_dispatch(command.approval_id)
        else:
            invocation = await self._get_invocation(approval.invocation_id)
        return DecideApprovalResult(_read_model(approval), invocation.status)

    async def _resume_and_dispatch(
        self,
        approval_id: ApprovalRequestId,
    ) -> tuple[ApprovalRequest, ToolInvocation]:
        now = self._clock.now()
        async with self._unit_of_work_factory() as unit_of_work:
            approval = await unit_of_work.approvals.get_request_for_update(approval_id)
            if approval is None:
                raise ApprovalNotFoundError("approval was not found")
            invocation = await unit_of_work.tool_invocations.get(approval.invocation_id)
            if invocation is None:
                raise ApprovalRevalidationError(_REVALIDATION_FAILED)
            if approval.status is ApprovalStatus.CONSUMED:
                return approval, invocation
            if approval.is_expired(at=now):
                expired = await self._cancel_locked(
                    unit_of_work=unit_of_work,
                    approval=approval.expire(at=now),
                    previous_approval=approval,
                    decision_actor_id=None,
                    decision="expired",
                    at=now,
                )
                await unit_of_work.commit()
                return expired, invocation.cancel(at=now)
            if approval.status is not ApprovalStatus.APPROVED:
                raise ApprovalDecisionConflictError("approval is not approved")

            eligible = await self._revalidate_locked(unit_of_work, approval, invocation)
            adapter = self._adapter_resolver.resolve(eligible.version.manifest.adapter_key)
            if adapter is None:
                raise ToolDispatchError(_ADAPTER_UNAVAILABLE)
            turn = await unit_of_work.turns.get(invocation.turn_id)
            attempt = await unit_of_work.turns.get_attempt(invocation.attempt_id)
            if (
                turn is None
                or attempt is None
                or turn.status is not TurnStatus.AWAITING_CONFIRMATION
                or attempt.status is not TurnAttemptStatus.AWAITING_CONFIRMATION
                or invocation.status is not ToolInvocationStatus.AWAITING_CONFIRMATION
            ):
                raise ApprovalRevalidationError(_REVALIDATION_FAILED)
            consumed = approval.consume(at=now)
            running = invocation.start(at=now)
            await unit_of_work.approvals.update_lifecycle(previous=approval, updated=consumed)
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=invocation, updated=running
            )
            await unit_of_work.turns.update_turn_lifecycle(previous=turn, updated=turn.resume())
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt, updated=attempt.resume()
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TOOL_STARTED,
                payload={
                    "invocation_id": str(invocation.id),
                    "tool_definition_id": str(invocation.tool_definition_id),
                    "tool_version_id": str(invocation.tool_version_id),
                },
                occurred_at=now,
            )
            await unit_of_work.commit()

        try:
            result = await asyncio.wait_for(
                adapter.invoke(
                    ToolInvocationRequest(
                        arguments=running.arguments,
                        idempotency_key=running.idempotency_key,
                    )
                ),
                timeout=eligible.version.manifest.timeout_ms / 1000,
            )
        except TimeoutError:
            return consumed, await self._fail_running(running, _TIMEOUT)
        except Exception:
            return consumed, await self._fail_running(running, _ADAPTER_ERROR)

        if isinstance(result, ToolInvocationFailure):
            return consumed, await self._fail_running(running, f"tool.{result.error_code}")
        if not isinstance(result, ToolInvocationSuccess):
            return consumed, await self._fail_running(running, _ADAPTER_ERROR)
        output = mutable_json_value(result.output)
        if not isinstance(output, dict) or self._schema_validator.validate_instance(
            instance=output,
            schema=eligible.version.manifest.output_schema,
        ):
            return consumed, await self._fail_running(running, _OUTPUT_INVALID)
        return consumed, await self._succeed_running(running, output)

    async def _revalidate_locked(
        self,
        unit_of_work: UnitOfWork,
        approval: ApprovalRequest,
        invocation: ToolInvocation,
    ) -> EligibleTool:
        evaluation = await unit_of_work.approvals.get_evaluation(approval.policy_evaluation_id)
        if evaluation is None:
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        eligible_tools = await unit_of_work.tools.list_eligible_for_agent(
            team_id=approval.team_id,
            agent_version_id=evaluation.agent_version_id,
        )
        eligible = next(
            (
                tool
                for tool in eligible_tools
                if tool.definition.id == invocation.tool_definition_id
                and tool.version.id == invocation.tool_version_id
            ),
            None,
        )
        if eligible is None:
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        state = await unit_of_work.tools.get_version_state_for_update(invocation.tool_version_id)
        turn = await unit_of_work.turns.get(invocation.turn_id)
        conversation = (
            None if turn is None else await unit_of_work.conversations.get(turn.conversation_id)
        )
        context = PolicyContext(
            team_id=evaluation.team_id,
            actor_id=evaluation.requester_actor_id,
            agent_version_id=evaluation.agent_version_id,
            tool_team_id=eligible.definition.team_id,
            tool_definition_id=eligible.definition.id,
            tool_version_id=eligible.version.id,
            effect=eligible.version.manifest.effect,
            required_scopes=eligible.version.manifest.required_scopes,
            granted_scopes=invocation.authorized_scopes,
            environment=evaluation.environment,
            arguments=invocation.arguments,
            is_bound=True,
            is_active=state is not None and state.status is ToolLifecycleStatus.ACTIVE,
            is_conformant=state is not None and state.activated_conformance_run_id is not None,
        )
        if (
            turn is None
            or conversation is None
            or conversation.team_id != approval.team_id
            or turn.agent_version_id != evaluation.agent_version_id
            or evaluation.team_id != approval.team_id
            or evaluation.requester_actor_id != approval.requester_actor_id
            or evaluation.invocation_id != invocation.id
            or evaluation.tool_definition_id != invocation.tool_definition_id
            or evaluation.tool_version_id != invocation.tool_version_id
            or evaluation.effect is not ToolEffect.MUTATING
            or evaluation.granted_scopes != invocation.authorized_scopes
            or evaluation.turn_id != invocation.turn_id
            or evaluation.attempt_id != invocation.attempt_id
            or evaluation.fingerprint != approval.fingerprint
            or evaluation.evaluation.decision is not PolicyDecision.REQUIRE_CONFIRMATION
            or evaluate_policy(context).decision is not PolicyDecision.REQUIRE_CONFIRMATION
            or fingerprint_action(context) != approval.fingerprint
            or eligible.version.manifest.effect is not ToolEffect.MUTATING
        ):
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        return eligible

    async def _cancel_locked(
        self,
        *,
        unit_of_work: UnitOfWork,
        approval: ApprovalRequest,
        previous_approval: ApprovalRequest,
        decision_actor_id: ActorId | None,
        decision: str,
        at: datetime,
    ) -> ApprovalRequest:
        invocation = await unit_of_work.tool_invocations.get(approval.invocation_id)
        if invocation is None:
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        turn = await unit_of_work.turns.get(invocation.turn_id)
        attempt = await unit_of_work.turns.get_attempt(invocation.attempt_id)
        if turn is None or attempt is None:
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        await unit_of_work.approvals.update_lifecycle(previous=previous_approval, updated=approval)
        await self._append_resolved(
            unit_of_work=unit_of_work,
            approval=approval,
            invocation=invocation,
            decision=decision,
            actor_id=decision_actor_id,
            at=at,
        )
        await unit_of_work.tool_invocations.update_lifecycle(
            previous=invocation, updated=invocation.cancel(at=at)
        )
        await unit_of_work.turns.update_attempt_lifecycle(
            previous=attempt, updated=attempt.cancel(at=at)
        )
        await unit_of_work.turns.update_turn_lifecycle(previous=turn, updated=turn.cancel(at=at))
        await unit_of_work.turns.append_event(
            turn_id=turn.id,
            event_id=self._event_ids.new(),
            attempt_id=attempt.id,
            kind=ExecutionEventKind.TURN_CANCELLED,
            payload={"reason": f"approval_{decision}"},
            occurred_at=at,
        )
        return approval

    async def _append_resolved(
        self,
        *,
        unit_of_work: UnitOfWork,
        approval: ApprovalRequest,
        invocation: ToolInvocation,
        decision: str,
        actor_id: ActorId | None,
        at: datetime,
    ) -> None:
        payload: dict[str, object] = {
            "approval_id": str(approval.id),
            "decision": decision,
            "resolved_at": at.isoformat(),
        }
        if actor_id is not None:
            payload["actor_id"] = str(actor_id)
        await unit_of_work.turns.append_event(
            turn_id=invocation.turn_id,
            event_id=self._event_ids.new(),
            attempt_id=invocation.attempt_id,
            kind=ExecutionEventKind.APPROVAL_RESOLVED,
            payload=payload,
            occurred_at=at,
        )

    async def _expire_and_cancel(
        self, *, approval_id: ApprovalRequestId, team_id: TeamId
    ) -> ApprovalRequest:
        async with self._unit_of_work_factory() as unit_of_work:
            approval = await unit_of_work.approvals.get_request_for_update(approval_id)
            if approval is None:
                raise ApprovalNotFoundError("approval was not found")
            if approval.team_id != team_id:
                raise ApprovalTeamMismatchError("approval belongs to another team")
            if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
                return approval
            now = self._clock.now()
            expired = await self._cancel_locked(
                unit_of_work=unit_of_work,
                approval=approval.expire(at=now),
                previous_approval=approval,
                decision_actor_id=None,
                decision="expired",
                at=now,
            )
            await unit_of_work.commit()
            return expired

    async def _load_for_team(
        self, *, team_id: TeamId, approval_id: ApprovalRequestId
    ) -> ApprovalRequest:
        async with self._unit_of_work_factory() as unit_of_work:
            approval = await unit_of_work.approvals.get_request(approval_id)
        if approval is None:
            raise ApprovalNotFoundError("approval was not found")
        if approval.team_id != team_id:
            raise ApprovalTeamMismatchError("approval belongs to another team")
        return approval

    async def _get_invocation(self, invocation_id: ToolInvocationId) -> ToolInvocation:
        async with self._unit_of_work_factory() as unit_of_work:
            invocation = await unit_of_work.tool_invocations.get(invocation_id)
        if invocation is None:
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        return invocation

    async def _require_invocation_in(
        self,
        unit_of_work: UnitOfWork,
        approval: ApprovalRequest,
    ) -> ToolInvocation:
        invocation = await unit_of_work.tool_invocations.get(approval.invocation_id)
        if invocation is None:
            raise ApprovalRevalidationError(_REVALIDATION_FAILED)
        return invocation

    async def _succeed_running(self, running: ToolInvocation, output: JsonObject) -> ToolInvocation:
        at = self._clock.now()
        succeeded = running.succeed(at=at, result=output)
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(running.turn_id)
            attempt = await unit_of_work.turns.get_attempt(running.attempt_id)
            if turn is None or attempt is None:
                raise ApprovalRevalidationError(_REVALIDATION_FAILED)
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=running, updated=succeeded
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TOOL_COMPLETED,
                payload={"invocation_id": str(running.id)},
                occurred_at=at,
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt, updated=attempt.succeed(at=at)
            )
            await unit_of_work.turns.update_turn_lifecycle(
                previous=turn, updated=turn.complete(at=at)
            )
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_COMPLETED,
                payload={},
                occurred_at=at,
            )
            await unit_of_work.commit()
        return succeeded

    async def _fail_running(self, running: ToolInvocation, failure_code: str) -> ToolInvocation:
        at = self._clock.now()
        failed = running.fail(at=at, failure_code=failure_code)
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(running.turn_id)
            attempt = await unit_of_work.turns.get_attempt(running.attempt_id)
            if turn is None or attempt is None:
                raise ApprovalRevalidationError(_REVALIDATION_FAILED)
            await unit_of_work.tool_invocations.update_lifecycle(previous=running, updated=failed)
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TOOL_FAILED,
                payload={"invocation_id": str(running.id), "failure_code": failure_code},
                occurred_at=at,
            )
            await unit_of_work.turns.update_attempt_lifecycle(
                previous=attempt,
                updated=attempt.fail(at=at, failure_code="agent_execution_failed"),
            )
            await unit_of_work.turns.update_turn_lifecycle(previous=turn, updated=turn.fail(at=at))
            await unit_of_work.turns.append_event(
                turn_id=turn.id,
                event_id=self._event_ids.new(),
                attempt_id=attempt.id,
                kind=ExecutionEventKind.TURN_FAILED,
                payload={"failure_code": "agent_execution_failed"},
                occurred_at=at,
            )
            await unit_of_work.commit()
        return failed


def _read_model(approval: ApprovalRequest) -> ApprovalReadModel:
    return ApprovalReadModel(
        approval_id=approval.id,
        invocation_id=approval.invocation_id,
        requester_actor_id=approval.requester_actor_id,
        status=approval.status,
        tool_definition_id=approval.safe_summary.tool_definition_id,
        tool_version_id=approval.safe_summary.tool_version_id,
        effect=approval.safe_summary.effect,
        argument_fields=approval.safe_summary.argument_fields,
        fingerprint_version=approval.fingerprint.version,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        resolved_by_actor_id=approval.resolved_by_actor_id,
        resolved_at=approval.resolved_at,
    )
