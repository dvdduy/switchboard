"""Translation between relational rows and pure domain entities."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    PolicyEvaluationRecord,
)
from switchboard.domain.command_receipts import (
    ApprovalDecision,
    CommandOperation,
    CommandReceipt,
)
from switchboard.domain.context import ContextPolicy, ConversationSummary
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    ActorId,
    AgentDefinitionId,
    AgentToolBindingId,
    AgentVersionId,
    ApprovalRequestId,
    CommandReceiptId,
    ConversationId,
    ConversationSummaryId,
    ExecutionEventId,
    MessageId,
    PolicyEvaluationId,
    TeamId,
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.json_values import mutable_json_value
from switchboard.domain.policy import (
    ActionFingerprint,
    PolicyDecision,
    PolicyEnvironment,
    PolicyEvaluation,
    PolicyReasonCode,
    SafeActionSummary,
)
from switchboard.domain.tool_invocations import (
    ToolInvocation,
    ToolInvocationStatus,
)
from switchboard.domain.tools import (
    AgentToolBinding,
    IdempotencyMode,
    ReconciliationMode,
    RetryPolicy,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolDefinition,
    ToolEffect,
    ToolLifecycleStatus,
    ToolManifest,
    ToolVersion,
    ToolVersionState,
)
from switchboard.domain.turns import (
    Turn,
    TurnAttempt,
    TurnAttemptStatus,
    TurnStatus,
)
from switchboard.domain.workflow_approvals import (
    WorkflowPlanActionSummary,
    WorkflowPlanApproval,
)
from switchboard.domain.workflows import (
    TurnWorkflow,
    WorkflowPlanFingerprint,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepKind,
    WorkflowStepStatus,
)


class Record(Protocol):
    """Record supporting lookup by relational column name."""

    def __getitem__(self, key: str, /) -> object: ...


def agent_definition_to_record(
    definition: AgentDefinition,
) -> dict[str, object]:
    return {
        "id": definition.id,
        "team_id": definition.team_id,
        "name": definition.name,
        "created_at": definition.created_at,
    }


def agent_definition_from_record(
    record: Record,
) -> AgentDefinition:
    return AgentDefinition(
        id=AgentDefinitionId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        name=cast(str, record["name"]),
        created_at=cast(datetime, record["created_at"]),
    )


def agent_version_to_record(
    version: AgentVersion,
) -> dict[str, object]:
    return {
        "id": version.id,
        "agent_definition_id": version.agent_definition_id,
        "version_number": version.version_number,
        "model_window_tokens": version.context_policy.model_window_tokens,
        "reserved_output_tokens": version.context_policy.reserved_output_tokens,
        "fixed_overhead_tokens": version.context_policy.fixed_overhead_tokens,
        "summary_max_tokens": version.context_policy.summary_max_tokens,
        "minimum_recent_messages": version.context_policy.minimum_recent_messages,
        "created_at": version.created_at,
    }


def agent_version_from_record(
    record: Record,
) -> AgentVersion:
    return AgentVersion(
        id=AgentVersionId(cast(UUID, record["id"])),
        agent_definition_id=AgentDefinitionId(cast(UUID, record["agent_definition_id"])),
        version_number=cast(int, record["version_number"]),
        context_policy=ContextPolicy(
            model_window_tokens=cast(int, record["model_window_tokens"]),
            reserved_output_tokens=cast(int, record["reserved_output_tokens"]),
            fixed_overhead_tokens=cast(int, record["fixed_overhead_tokens"]),
            summary_max_tokens=cast(int, record["summary_max_tokens"]),
            minimum_recent_messages=cast(int, record["minimum_recent_messages"]),
        ),
        created_at=cast(datetime, record["created_at"]),
    )


def conversation_summary_to_record(
    summary: ConversationSummary,
) -> dict[str, object]:
    return {
        "id": summary.id,
        "conversation_id": summary.conversation_id,
        "agent_version_id": summary.agent_version_id,
        "from_sequence": summary.from_sequence,
        "through_sequence": summary.through_sequence,
        "content": summary.content,
        "estimated_token_count": summary.estimated_token_count,
        "summarizer_version": summary.summarizer_version,
        "token_counter_version": summary.token_counter_version,
        "created_at": summary.created_at,
    }


def conversation_summary_from_record(
    record: Record,
) -> ConversationSummary:
    return ConversationSummary(
        id=ConversationSummaryId(cast(UUID, record["id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        agent_version_id=AgentVersionId(cast(UUID, record["agent_version_id"])),
        from_sequence=cast(int, record["from_sequence"]),
        through_sequence=cast(int, record["through_sequence"]),
        content=cast(str, record["content"]),
        estimated_token_count=cast(int, record["estimated_token_count"]),
        summarizer_version=cast(str, record["summarizer_version"]),
        token_counter_version=cast(str, record["token_counter_version"]),
        created_at=cast(datetime, record["created_at"]),
    )


def conversation_to_record(
    conversation: Conversation,
) -> dict[str, object]:
    return {
        "id": conversation.id,
        "team_id": conversation.team_id,
        "default_agent_version_id": conversation.default_agent_version_id,
        "status": conversation.status.value,
        "next_message_sequence": conversation.next_message_sequence,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def conversation_from_record(
    record: Record,
) -> Conversation:
    return Conversation(
        id=ConversationId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        default_agent_version_id=AgentVersionId(cast(UUID, record["default_agent_version_id"])),
        status=ConversationStatus(cast(str, record["status"])),
        next_message_sequence=cast(
            int,
            record["next_message_sequence"],
        ),
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


def message_to_record(
    message: Message,
) -> dict[str, object]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sequence": message.sequence,
        "role": message.role.value,
        "content": message.content,
        "created_at": message.created_at,
    }


def message_from_record(
    record: Record,
) -> Message:
    return Message(
        id=MessageId(cast(UUID, record["id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        sequence=cast(int, record["sequence"]),
        role=MessageRole(cast(str, record["role"])),
        content=cast(str, record["content"]),
        created_at=cast(datetime, record["created_at"]),
    )


def command_receipt_to_record(receipt: CommandReceipt) -> dict[str, object]:
    return {
        "id": receipt.id,
        "team_id": receipt.team_id,
        "operation": receipt.operation.value,
        "command_scope": receipt.command_scope,
        "idempotency_key_hash": receipt.idempotency_key_hash,
        "request_fingerprint": receipt.request_fingerprint,
        "conversation_id": receipt.conversation_id,
        "message_id": receipt.message_id,
        "turn_id": receipt.turn_id,
        "attempt_id": receipt.attempt_id,
        "approval_id": receipt.approval_id,
        "actor_id": receipt.actor_id,
        "approval_decision": (
            None if receipt.approval_decision is None else receipt.approval_decision.value
        ),
        "created_at": receipt.created_at,
    }


def command_receipt_from_record(record: Record) -> CommandReceipt:
    return CommandReceipt(
        id=CommandReceiptId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        operation=CommandOperation(cast(str, record["operation"])),
        command_scope=cast(str, record["command_scope"]),
        idempotency_key_hash=cast(str, record["idempotency_key_hash"]),
        request_fingerprint=cast(str, record["request_fingerprint"]),
        conversation_id=(
            None
            if record["conversation_id"] is None
            else ConversationId(cast(UUID, record["conversation_id"]))
        ),
        message_id=(
            None if record["message_id"] is None else MessageId(cast(UUID, record["message_id"]))
        ),
        turn_id=None if record["turn_id"] is None else TurnId(cast(UUID, record["turn_id"])),
        attempt_id=(
            None
            if record["attempt_id"] is None
            else TurnAttemptId(cast(UUID, record["attempt_id"]))
        ),
        approval_id=(
            None
            if record["approval_id"] is None
            else ApprovalRequestId(cast(UUID, record["approval_id"]))
        ),
        actor_id=(None if record["actor_id"] is None else ActorId(cast(UUID, record["actor_id"]))),
        approval_decision=(
            None
            if record["approval_decision"] is None
            else ApprovalDecision(cast(str, record["approval_decision"]))
        ),
        created_at=cast(datetime, record["created_at"]),
    )


def turn_to_record(
    turn: Turn,
) -> dict[str, object]:
    return {
        "id": turn.id,
        "conversation_id": turn.conversation_id,
        "input_message_id": turn.input_message_id,
        "agent_version_id": turn.agent_version_id,
        "status": turn.status.value,
        "created_at": turn.created_at,
        "completed_at": turn.completed_at,
        "next_event_sequence": turn.next_event_sequence,
    }


def turn_from_record(
    record: Record,
) -> Turn:
    return Turn(
        id=TurnId(cast(UUID, record["id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        input_message_id=MessageId(cast(UUID, record["input_message_id"])),
        agent_version_id=AgentVersionId(cast(UUID, record["agent_version_id"])),
        status=TurnStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        completed_at=cast(
            datetime | None,
            record["completed_at"],
        ),
        next_event_sequence=cast(
            int,
            record["next_event_sequence"],
        ),
    )


def turn_attempt_to_record(
    attempt: TurnAttempt,
) -> dict[str, object]:
    return {
        "id": attempt.id,
        "turn_id": attempt.turn_id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status.value,
        "created_at": attempt.created_at,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "failure_code": attempt.failure_code,
    }


def turn_attempt_from_record(
    record: Record,
) -> TurnAttempt:
    return TurnAttempt(
        id=TurnAttemptId(cast(UUID, record["id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_number=cast(int, record["attempt_number"]),
        status=TurnAttemptStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast(
            datetime | None,
            record["started_at"],
        ),
        completed_at=cast(
            datetime | None,
            record["completed_at"],
        ),
        failure_code=cast(
            str | None,
            record["failure_code"],
        ),
    )


def _thaw_json_value(value: object) -> object:
    """Convert immutable domain JSON into serializer-friendly values."""

    if isinstance(value, Mapping):
        return {key: _thaw_json_value(nested_value) for key, nested_value in value.items()}

    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]

    return value


def thaw_json_object(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Convert a frozen JSON object to a mutable database value."""

    return {key: _thaw_json_value(value) for key, value in payload.items()}


def execution_event_to_record(
    event: ExecutionEvent,
) -> dict[str, object]:
    return {
        "id": event.id,
        "turn_id": event.turn_id,
        "attempt_id": event.attempt_id,
        "sequence": event.sequence,
        "kind": event.kind.value,
        "payload": thaw_json_object(event.payload),
        "occurred_at": event.occurred_at,
    }


def execution_event_from_record(
    record: Record,
) -> ExecutionEvent:
    return ExecutionEvent(
        id=ExecutionEventId(cast(UUID, record["id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_id=(
            None
            if record["attempt_id"] is None
            else TurnAttemptId(cast(UUID, record["attempt_id"]))
        ),
        sequence=cast(int, record["sequence"]),
        kind=ExecutionEventKind(cast(str, record["kind"])),
        payload=cast(
            Mapping[str, object],
            record["payload"],
        ),
        occurred_at=cast(
            datetime,
            record["occurred_at"],
        ),
    )


def tool_manifest_to_record(manifest: ToolManifest) -> dict[str, object]:
    """Serialize one validated manifest for JSONB persistence."""

    return cast(
        dict[str, object],
        mutable_json_value(
            {
                "schema_version": manifest.schema_version,
                "display_name": manifest.display_name,
                "description": manifest.description,
                "input_schema": manifest.input_schema,
                "output_schema": manifest.output_schema,
                "effect": manifest.effect.value,
                "required_scopes": manifest.required_scopes,
                "timeout_ms": manifest.timeout_ms,
                "retry_policy": {
                    "max_attempts": manifest.retry_policy.max_attempts,
                    "initial_backoff_ms": manifest.retry_policy.initial_backoff_ms,
                    "retryable_error_codes": manifest.retry_policy.retryable_error_codes,
                },
                "idempotency": manifest.idempotency.value,
                "reconciliation": manifest.reconciliation.value,
                "adapter_key": manifest.adapter_key,
                "sensitive_input_paths": manifest.sensitive_input_paths,
                "sensitive_output_paths": manifest.sensitive_output_paths,
            }
        ),
    )


def tool_manifest_from_record(value: object) -> ToolManifest:
    record = cast(Mapping[str, object], value)
    retry = cast(Mapping[str, object], record["retry_policy"])
    return ToolManifest(
        schema_version=cast(str, record["schema_version"]),
        display_name=cast(str, record["display_name"]),
        description=cast(str, record["description"]),
        input_schema=cast(Mapping[str, object], record["input_schema"]),
        output_schema=cast(Mapping[str, object], record["output_schema"]),
        effect=ToolEffect(cast(str, record["effect"])),
        required_scopes=tuple(cast(list[str], record["required_scopes"])),
        timeout_ms=cast(int, record["timeout_ms"]),
        retry_policy=RetryPolicy(
            max_attempts=cast(int, retry["max_attempts"]),
            initial_backoff_ms=cast(int, retry["initial_backoff_ms"]),
            retryable_error_codes=tuple(cast(list[str], retry["retryable_error_codes"])),
        ),
        idempotency=IdempotencyMode(cast(str, record["idempotency"])),
        reconciliation=ReconciliationMode(cast(str, record["reconciliation"])),
        adapter_key=cast(str, record["adapter_key"]),
        sensitive_input_paths=tuple(cast(list[str], record["sensitive_input_paths"])),
        sensitive_output_paths=tuple(cast(list[str], record["sensitive_output_paths"])),
    )


def tool_definition_to_record(definition: ToolDefinition) -> dict[str, object]:
    return {
        "id": definition.id,
        "team_id": definition.team_id,
        "tool_key": definition.tool_key,
        "created_at": definition.created_at,
    }


def tool_definition_from_record(record: Record) -> ToolDefinition:
    return ToolDefinition(
        id=ToolDefinitionId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        tool_key=cast(str, record["tool_key"]),
        created_at=cast(datetime, record["created_at"]),
    )


def tool_version_to_record(version: ToolVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "tool_definition_id": version.tool_definition_id,
        "version_number": version.version_number,
        "manifest": tool_manifest_to_record(version.manifest),
        "content_hash": version.content_hash,
        "created_at": version.created_at,
    }


def tool_version_from_record(record: Record) -> ToolVersion:
    return ToolVersion(
        id=ToolVersionId(cast(UUID, record["id"])),
        tool_definition_id=ToolDefinitionId(cast(UUID, record["tool_definition_id"])),
        version_number=cast(int, record["version_number"]),
        manifest=tool_manifest_from_record(record["manifest"]),
        content_hash=cast(str, record["content_hash"]),
        created_at=cast(datetime, record["created_at"]),
    )


def tool_invocation_to_record(invocation: ToolInvocation) -> dict[str, object]:
    return {
        "id": invocation.id,
        "turn_id": invocation.turn_id,
        "attempt_id": invocation.attempt_id,
        "invocation_number": invocation.invocation_number,
        "tool_definition_id": invocation.tool_definition_id,
        "tool_version_id": invocation.tool_version_id,
        "arguments": mutable_json_value(invocation.arguments),
        "idempotency_key": invocation.idempotency_key,
        "authorized_scopes": list(invocation.authorized_scopes),
        "status": invocation.status.value,
        "created_at": invocation.created_at,
        "started_at": invocation.started_at,
        "completed_at": invocation.completed_at,
        "result": (None if invocation.result is None else mutable_json_value(invocation.result)),
        "failure_code": invocation.failure_code,
    }


def tool_invocation_from_record(record: Record) -> ToolInvocation:
    result = record["result"]
    return ToolInvocation(
        id=ToolInvocationId(cast(UUID, record["id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_id=TurnAttemptId(cast(UUID, record["attempt_id"])),
        invocation_number=cast(int, record["invocation_number"]),
        tool_definition_id=ToolDefinitionId(cast(UUID, record["tool_definition_id"])),
        tool_version_id=ToolVersionId(cast(UUID, record["tool_version_id"])),
        arguments=cast(Mapping[str, object], record["arguments"]),
        idempotency_key=cast(str, record["idempotency_key"]),
        authorized_scopes=tuple(cast(list[str], record["authorized_scopes"])),
        status=ToolInvocationStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast(datetime | None, record["started_at"]),
        completed_at=cast(datetime | None, record["completed_at"]),
        result=None if result is None else cast(Mapping[str, object], result),
        failure_code=cast(str | None, record["failure_code"]),
    )


def turn_workflow_to_record(workflow: TurnWorkflow) -> dict[str, object]:
    fingerprint = workflow.plan_fingerprint
    return {
        "id": workflow.id,
        "turn_id": workflow.turn_id,
        "attempt_id": workflow.attempt_id,
        "status": workflow.status.value,
        "plan_version": workflow.plan_version,
        "plan_fingerprint_version": None if fingerprint is None else fingerprint.version,
        "plan_fingerprint_digest": None if fingerprint is None else fingerprint.digest,
        "approval_id": workflow.approval_id,
        "output_message_id": workflow.output_message_id,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "completed_at": workflow.completed_at,
    }


def turn_workflow_from_record(record: Record) -> TurnWorkflow:
    fingerprint_version = record["plan_fingerprint_version"]
    fingerprint_digest = record["plan_fingerprint_digest"]
    approval_id = record["approval_id"]
    output_message_id = record["output_message_id"]
    return TurnWorkflow(
        id=TurnWorkflowId(cast(UUID, record["id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_id=TurnAttemptId(cast(UUID, record["attempt_id"])),
        status=WorkflowStatus(cast(str, record["status"])),
        plan_version=cast(int, record["plan_version"]),
        plan_fingerprint=(
            None
            if fingerprint_version is None or fingerprint_digest is None
            else WorkflowPlanFingerprint(
                version=cast(str, fingerprint_version),
                digest=cast(str, fingerprint_digest),
            )
        ),
        approval_id=(
            None if approval_id is None else WorkflowPlanApprovalId(cast(UUID, approval_id))
        ),
        output_message_id=(
            None if output_message_id is None else MessageId(cast(UUID, output_message_id))
        ),
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
        completed_at=cast(datetime | None, record["completed_at"]),
    )


def workflow_step_to_record(step: WorkflowStep) -> dict[str, object]:
    return {
        "id": step.id,
        "workflow_id": step.workflow_id,
        "turn_id": step.turn_id,
        "attempt_id": step.attempt_id,
        "step_number": step.step_number,
        "kind": step.kind.value,
        "status": step.status.value,
        "predecessor_step_id": step.predecessor_step_id,
        "predecessor_step_number": step.predecessor_step_number,
        "invocation_id": step.invocation_id,
        "output_message_id": step.output_message_id,
        "created_at": step.created_at,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
        "failure_code": step.failure_code,
    }


def workflow_step_from_record(record: Record) -> WorkflowStep:
    predecessor_step_id = record["predecessor_step_id"]
    invocation_id = record["invocation_id"]
    output_message_id = record["output_message_id"]
    return WorkflowStep(
        id=WorkflowStepId(cast(UUID, record["id"])),
        workflow_id=TurnWorkflowId(cast(UUID, record["workflow_id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_id=TurnAttemptId(cast(UUID, record["attempt_id"])),
        step_number=cast(int, record["step_number"]),
        kind=WorkflowStepKind(cast(str, record["kind"])),
        status=WorkflowStepStatus(cast(str, record["status"])),
        predecessor_step_id=(
            None if predecessor_step_id is None else WorkflowStepId(cast(UUID, predecessor_step_id))
        ),
        predecessor_step_number=cast(int | None, record["predecessor_step_number"]),
        invocation_id=(
            None if invocation_id is None else ToolInvocationId(cast(UUID, invocation_id))
        ),
        output_message_id=(
            None if output_message_id is None else MessageId(cast(UUID, output_message_id))
        ),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast(datetime | None, record["started_at"]),
        completed_at=cast(datetime | None, record["completed_at"]),
        failure_code=cast(str | None, record["failure_code"]),
    )


def workflow_plan_approval_to_record(
    approval: WorkflowPlanApproval,
) -> dict[str, object]:
    return {
        "id": approval.id,
        "workflow_id": approval.workflow_id,
        "team_id": approval.team_id,
        "requester_actor_id": approval.requester_actor_id,
        "fingerprint_version": approval.fingerprint.version,
        "fingerprint_digest": approval.fingerprint.digest,
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
        "status": approval.status.value,
        "created_at": approval.created_at,
        "expires_at": approval.expires_at,
        "resolved_by_actor_id": approval.resolved_by_actor_id,
        "resolved_at": approval.resolved_at,
        "consumed_at": approval.consumed_at,
    }


def workflow_plan_approval_from_record(record: Record) -> WorkflowPlanApproval:
    resolved_actor_id = record["resolved_by_actor_id"]
    raw_actions = cast(list[dict[str, object]], record["safe_actions"])
    return WorkflowPlanApproval(
        id=WorkflowPlanApprovalId(cast(UUID, record["id"])),
        workflow_id=TurnWorkflowId(cast(UUID, record["workflow_id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        requester_actor_id=ActorId(cast(UUID, record["requester_actor_id"])),
        fingerprint=WorkflowPlanFingerprint(
            version=cast(str, record["fingerprint_version"]),
            digest=cast(str, record["fingerprint_digest"]),
        ),
        safe_actions=tuple(
            WorkflowPlanActionSummary(
                step_number=cast(int, action["step_number"]),
                action=SafeActionSummary(
                    tool_definition_id=ToolDefinitionId(
                        UUID(cast(str, action["tool_definition_id"]))
                    ),
                    tool_version_id=ToolVersionId(UUID(cast(str, action["tool_version_id"]))),
                    effect=ToolEffect(cast(str, action["effect"])),
                    argument_fields=tuple(cast(list[str], action["argument_fields"])),
                ),
            )
            for action in raw_actions
        ),
        status=ApprovalStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        expires_at=cast(datetime, record["expires_at"]),
        resolved_by_actor_id=(
            None if resolved_actor_id is None else ActorId(cast(UUID, resolved_actor_id))
        ),
        resolved_at=cast(datetime | None, record["resolved_at"]),
        consumed_at=cast(datetime | None, record["consumed_at"]),
    )


def policy_evaluation_to_record(evaluation: PolicyEvaluationRecord) -> dict[str, object]:
    return {
        "id": evaluation.id,
        "team_id": evaluation.team_id,
        "requester_actor_id": evaluation.requester_actor_id,
        "agent_version_id": evaluation.agent_version_id,
        "turn_id": evaluation.turn_id,
        "attempt_id": evaluation.attempt_id,
        "invocation_id": evaluation.invocation_id,
        "tool_definition_id": evaluation.tool_definition_id,
        "tool_version_id": evaluation.tool_version_id,
        "effect": evaluation.effect.value,
        "environment": evaluation.environment.value,
        "required_scopes": list(evaluation.required_scopes),
        "granted_scopes": list(evaluation.granted_scopes),
        "policy_version": evaluation.evaluation.policy_version,
        "decision": evaluation.evaluation.decision.value,
        "reason_code": evaluation.evaluation.reason_code.value,
        "fingerprint_version": evaluation.fingerprint.version,
        "fingerprint_digest": evaluation.fingerprint.digest,
        "evaluated_at": evaluation.evaluated_at,
    }


def policy_evaluation_from_record(record: Record) -> PolicyEvaluationRecord:
    invocation_id = record["invocation_id"]
    return PolicyEvaluationRecord(
        id=PolicyEvaluationId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        requester_actor_id=ActorId(cast(UUID, record["requester_actor_id"])),
        agent_version_id=AgentVersionId(cast(UUID, record["agent_version_id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_id=TurnAttemptId(cast(UUID, record["attempt_id"])),
        invocation_id=(
            None if invocation_id is None else ToolInvocationId(cast(UUID, invocation_id))
        ),
        tool_definition_id=ToolDefinitionId(cast(UUID, record["tool_definition_id"])),
        tool_version_id=ToolVersionId(cast(UUID, record["tool_version_id"])),
        effect=ToolEffect(cast(str, record["effect"])),
        environment=PolicyEnvironment(cast(str, record["environment"])),
        required_scopes=tuple(cast(list[str], record["required_scopes"])),
        granted_scopes=tuple(cast(list[str], record["granted_scopes"])),
        evaluation=PolicyEvaluation(
            policy_version=cast(str, record["policy_version"]),
            decision=PolicyDecision(cast(str, record["decision"])),
            reason_code=PolicyReasonCode(cast(str, record["reason_code"])),
        ),
        fingerprint=ActionFingerprint(
            version=cast(str, record["fingerprint_version"]),
            digest=cast(str, record["fingerprint_digest"]),
        ),
        evaluated_at=cast(datetime, record["evaluated_at"]),
    )


def approval_request_to_record(approval: ApprovalRequest) -> dict[str, object]:
    return {
        "id": approval.id,
        "team_id": approval.team_id,
        "policy_evaluation_id": approval.policy_evaluation_id,
        "invocation_id": approval.invocation_id,
        "requester_actor_id": approval.requester_actor_id,
        "fingerprint_version": approval.fingerprint.version,
        "fingerprint_digest": approval.fingerprint.digest,
        "tool_definition_id": approval.safe_summary.tool_definition_id,
        "tool_version_id": approval.safe_summary.tool_version_id,
        "effect": approval.safe_summary.effect.value,
        "argument_fields": list(approval.safe_summary.argument_fields),
        "status": approval.status.value,
        "created_at": approval.created_at,
        "expires_at": approval.expires_at,
        "resolved_by_actor_id": approval.resolved_by_actor_id,
        "resolved_at": approval.resolved_at,
        "consumed_at": approval.consumed_at,
    }


def approval_request_from_record(record: Record) -> ApprovalRequest:
    resolved_actor_id = record["resolved_by_actor_id"]
    return ApprovalRequest(
        id=ApprovalRequestId(cast(UUID, record["id"])),
        team_id=TeamId(cast(UUID, record["team_id"])),
        policy_evaluation_id=PolicyEvaluationId(cast(UUID, record["policy_evaluation_id"])),
        invocation_id=ToolInvocationId(cast(UUID, record["invocation_id"])),
        requester_actor_id=ActorId(cast(UUID, record["requester_actor_id"])),
        fingerprint=ActionFingerprint(
            version=cast(str, record["fingerprint_version"]),
            digest=cast(str, record["fingerprint_digest"]),
        ),
        safe_summary=SafeActionSummary(
            tool_definition_id=ToolDefinitionId(cast(UUID, record["tool_definition_id"])),
            tool_version_id=ToolVersionId(cast(UUID, record["tool_version_id"])),
            effect=ToolEffect(cast(str, record["effect"])),
            argument_fields=tuple(cast(list[str], record["argument_fields"])),
        ),
        status=ApprovalStatus(cast(str, record["status"])),
        created_at=cast(datetime, record["created_at"]),
        expires_at=cast(datetime, record["expires_at"]),
        resolved_by_actor_id=(
            None if resolved_actor_id is None else ActorId(cast(UUID, resolved_actor_id))
        ),
        resolved_at=cast(datetime | None, record["resolved_at"]),
        consumed_at=cast(datetime | None, record["consumed_at"]),
    )


def tool_version_state_to_record(state: ToolVersionState) -> dict[str, object]:
    return {
        "tool_version_id": state.tool_version_id,
        "status": state.status.value,
        "revision": state.revision,
        "activated_conformance_run_id": state.activated_conformance_run_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def tool_version_state_from_record(record: Record) -> ToolVersionState:
    run_id = record["activated_conformance_run_id"]
    return ToolVersionState(
        tool_version_id=ToolVersionId(cast(UUID, record["tool_version_id"])),
        status=ToolLifecycleStatus(cast(str, record["status"])),
        revision=cast(int, record["revision"]),
        activated_conformance_run_id=(
            None if run_id is None else ToolConformanceRunId(cast(UUID, run_id))
        ),
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


def agent_tool_binding_to_record(binding: AgentToolBinding) -> dict[str, object]:
    return {
        "id": binding.id,
        "agent_version_id": binding.agent_version_id,
        "tool_definition_id": binding.tool_definition_id,
        "tool_version_id": binding.tool_version_id,
        "created_at": binding.created_at,
    }


def agent_tool_binding_from_record(record: Record) -> AgentToolBinding:
    return AgentToolBinding(
        id=AgentToolBindingId(cast(UUID, record["id"])),
        agent_version_id=AgentVersionId(cast(UUID, record["agent_version_id"])),
        tool_definition_id=ToolDefinitionId(cast(UUID, record["tool_definition_id"])),
        tool_version_id=ToolVersionId(cast(UUID, record["tool_version_id"])),
        created_at=cast(datetime, record["created_at"]),
    )


def tool_conformance_run_to_record(run: ToolConformanceRun) -> dict[str, object]:
    return {
        "id": run.id,
        "tool_version_id": run.tool_version_id,
        "status": run.status.value,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def tool_conformance_run_from_record(record: Record) -> ToolConformanceRun:
    return ToolConformanceRun(
        id=ToolConformanceRunId(cast(UUID, record["id"])),
        tool_version_id=ToolVersionId(cast(UUID, record["tool_version_id"])),
        status=ToolConformanceStatus(cast(str, record["status"])),
        started_at=cast(datetime, record["started_at"]),
        completed_at=cast(datetime, record["completed_at"]),
    )


def tool_conformance_case_result_to_record(
    result: ToolConformanceCaseResult,
) -> dict[str, object]:
    return {
        "id": result.id,
        "run_id": result.run_id,
        "case_key": result.case_key,
        "status": result.status.value,
        "duration_ms": result.duration_ms,
        "diagnostic_code": result.diagnostic_code,
    }


def tool_conformance_case_result_from_record(record: Record) -> ToolConformanceCaseResult:
    return ToolConformanceCaseResult(
        id=ToolConformanceCaseResultId(cast(UUID, record["id"])),
        run_id=ToolConformanceRunId(cast(UUID, record["run_id"])),
        case_key=cast(str, record["case_key"]),
        status=ToolConformanceStatus(cast(str, record["status"])),
        duration_ms=cast(int, record["duration_ms"]),
        diagnostic_code=cast(str | None, record["diagnostic_code"]),
    )
