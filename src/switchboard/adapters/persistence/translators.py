"""Translation between relational rows and pure domain entities."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.command_receipts import CommandOperation, CommandReceipt
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
    AgentDefinitionId,
    AgentToolBindingId,
    AgentVersionId,
    CommandReceiptId,
    ConversationId,
    ConversationSummaryId,
    ExecutionEventId,
    MessageId,
    TeamId,
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.json_values import mutable_json_value
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
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        message_id=MessageId(cast(UUID, record["message_id"])),
        turn_id=TurnId(cast(UUID, record["turn_id"])),
        attempt_id=TurnAttemptId(cast(UUID, record["attempt_id"])),
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
