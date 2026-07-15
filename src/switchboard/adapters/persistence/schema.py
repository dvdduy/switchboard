"""Relational schema for Switchboard's durable domain state."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": ("fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"),
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


agent_definitions = Table(
    "agent_definitions",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "team_id",
        Uuid(as_uuid=True),
        nullable=False,
    ),
    Column(
        "name",
        String(200),
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    CheckConstraint(
        "btrim(name) <> ''",
        name="name_not_blank",
    ),
)


agent_versions = Table(
    "agent_versions",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "agent_definition_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "agent_definitions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column(
        "version_number",
        Integer,
        nullable=False,
    ),
    Column("model_window_tokens", Integer, nullable=False),
    Column("reserved_output_tokens", Integer, nullable=False),
    Column("fixed_overhead_tokens", Integer, nullable=False),
    Column("summary_max_tokens", Integer, nullable=False),
    Column("minimum_recent_messages", Integer, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    UniqueConstraint(
        "agent_definition_id",
        "version_number",
        name="agent_definition_version",
    ),
    CheckConstraint(
        "version_number > 0",
        name="version_number_positive",
    ),
    CheckConstraint(
        """
        model_window_tokens > 0
        AND reserved_output_tokens > 0
        AND fixed_overhead_tokens > 0
        AND summary_max_tokens > 0
        AND minimum_recent_messages > 0
        """,
        name="context_policy_fields_positive",
    ),
    CheckConstraint(
        "reserved_output_tokens + fixed_overhead_tokens < model_window_tokens",
        name="context_policy_has_input_capacity",
    ),
    CheckConstraint(
        """
        summary_max_tokens
        < model_window_tokens - reserved_output_tokens - fixed_overhead_tokens
        """,
        name="summary_fits_input_capacity",
    ),
)


conversations = Table(
    "conversations",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "team_id",
        Uuid(as_uuid=True),
        nullable=False,
    ),
    Column(
        "default_agent_version_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "agent_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column(
        "status",
        String(32),
        nullable=False,
    ),
    Column(
        "next_message_sequence",
        Integer,
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    CheckConstraint(
        "status IN ('active', 'closed')",
        name="status_valid",
    ),
    CheckConstraint(
        "next_message_sequence > 0",
        name="next_message_sequence_positive",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="updated_at_not_before_created_at",
    ),
)


messages = Table(
    "messages",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "conversation_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "conversations.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column(
        "sequence",
        Integer,
        nullable=False,
    ),
    Column(
        "role",
        String(32),
        nullable=False,
    ),
    Column(
        "content",
        Text,
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    UniqueConstraint(
        "conversation_id",
        "sequence",
        name="conversation_message_sequence",
    ),
    # Required by the composite foreign key from turns.
    UniqueConstraint(
        "conversation_id",
        "id",
        name="conversation_message_identity",
    ),
    CheckConstraint(
        "sequence > 0",
        name="sequence_positive",
    ),
    CheckConstraint(
        "role IN ('user', 'assistant')",
        name="role_valid",
    ),
    CheckConstraint(
        "btrim(content) <> ''",
        name="content_not_blank",
    ),
)


conversation_summaries = Table(
    "conversation_summaries",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("conversation_id", Uuid(as_uuid=True), nullable=False),
    Column("agent_version_id", Uuid(as_uuid=True), nullable=False),
    Column("from_sequence", Integer, nullable=False),
    Column("through_sequence", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("estimated_token_count", Integer, nullable=False),
    Column("summarizer_version", String(100), nullable=False),
    Column("token_counter_version", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["conversation_id", "from_sequence"],
        ["messages.conversation_id", "messages.sequence"],
        name="summary_from_message",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["conversation_id", "through_sequence"],
        ["messages.conversation_id", "messages.sequence"],
        name="summary_through_message",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["agent_version_id"],
        ["agent_versions.id"],
        name="summary_agent_version",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "conversation_id",
        "agent_version_id",
        "from_sequence",
        "through_sequence",
        "summarizer_version",
        "token_counter_version",
        name="conversation_summary_authority",
    ),
    CheckConstraint("from_sequence = 1", name="coverage_starts_at_one"),
    CheckConstraint(
        "through_sequence >= from_sequence",
        name="coverage_ordered",
    ),
    CheckConstraint(
        "estimated_token_count > 0",
        name="estimated_token_count_positive",
    ),
    CheckConstraint("btrim(content) <> ''", name="content_not_blank"),
    CheckConstraint(
        "btrim(summarizer_version) <> ''",
        name="summarizer_version_not_blank",
    ),
    CheckConstraint(
        "btrim(token_counter_version) <> ''",
        name="token_counter_version_not_blank",
    ),
)


turns = Table(
    "turns",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "conversation_id",
        Uuid(as_uuid=True),
        nullable=False,
    ),
    Column(
        "input_message_id",
        Uuid(as_uuid=True),
        nullable=False,
    ),
    Column(
        "agent_version_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "agent_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column(
        "status",
        String(32),
        nullable=False,
    ),
    Column(
        "next_event_sequence",
        Integer,
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    Column(
        "completed_at",
        DateTime(timezone=True),
        nullable=True,
    ),
    ForeignKeyConstraint(
        ["conversation_id", "input_message_id"],
        ["messages.conversation_id", "messages.id"],
        name="turn_input_message",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "input_message_id",
        name="input_message_turn",
    ),
    CheckConstraint(
        (
            "status IN ('received', 'running', 'awaiting_confirmation', "
            "'completed', 'failed', 'cancelled')"
        ),
        name="status_valid",
    ),
    CheckConstraint(
        """
        (
            status IN ('received', 'running', 'awaiting_confirmation')
            AND completed_at IS NULL
        )
        OR
        (
            status IN ('completed', 'failed', 'cancelled')
            AND completed_at IS NOT NULL
        )
        """,
        name="completion_matches_status",
    ),
    CheckConstraint(
        "completed_at IS NULL OR completed_at >= created_at",
        name="completed_at_not_before_created_at",
    ),
    CheckConstraint(
        "next_event_sequence > 0",
        name="next_event_sequence_positive",
    ),
)


turn_attempts = Table(
    "turn_attempts",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "turn_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "turns.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column(
        "attempt_number",
        Integer,
        nullable=False,
    ),
    Column(
        "status",
        String(32),
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=True,
    ),
    Column(
        "completed_at",
        DateTime(timezone=True),
        nullable=True,
    ),
    Column(
        "failure_code",
        String(100),
        nullable=True,
    ),
    UniqueConstraint(
        "turn_id",
        "attempt_number",
        name="turn_attempt_number",
    ),
    CheckConstraint(
        "attempt_number > 0",
        name="attempt_number_positive",
    ),
    CheckConstraint(
        (
            "status IN ('pending', 'running', 'awaiting_confirmation', "
            "'succeeded', 'failed', 'cancelled')"
        ),
        name="status_valid",
    ),
    CheckConstraint(
        """
        (
            status = 'pending'
            AND started_at IS NULL
            AND completed_at IS NULL
            AND failure_code IS NULL
        )
        OR
        (
            status IN ('running', 'awaiting_confirmation')
            AND started_at IS NOT NULL
            AND completed_at IS NULL
            AND failure_code IS NULL
        )
        OR
        (
            status = 'succeeded'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND failure_code IS NULL
        )
        OR
        (
            status = 'failed'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND failure_code IS NOT NULL
            AND btrim(failure_code) <> ''
        )
        OR
        (
            status = 'cancelled'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND failure_code IS NULL
        )
        """,
        name="lifecycle_fields_match_status",
    ),
    CheckConstraint(
        "started_at IS NULL OR started_at >= created_at",
        name="started_at_not_before_created_at",
    ),
    CheckConstraint(
        ("completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)"),
        name="completed_at_not_before_started_at",
    ),
    UniqueConstraint(
        "turn_id",
        "id",
        name="turn_attempt_identity",
    ),
)


command_receipts = Table(
    "command_receipts",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("team_id", Uuid(as_uuid=True), nullable=False),
    Column("operation", String(32), nullable=False),
    Column("command_scope", String(36), nullable=False),
    Column("idempotency_key_hash", String(64), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column(
        "conversation_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "conversations.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    ),
    Column(
        "message_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "messages.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    ),
    Column(
        "turn_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "turns.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    ),
    Column(
        "attempt_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "turn_attempts.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    ),
    Column(
        "approval_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "workflow_plan_approvals.id",
            name="fk_turn_workflows_approval_id_workflow_plan_approvals",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    ),
    Column("actor_id", Uuid(as_uuid=True), nullable=True),
    Column("approval_decision", String(16), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "team_id",
        "operation",
        "command_scope",
        "idempotency_key_hash",
        name="command_receipt_authority",
    ),
    CheckConstraint(
        "operation IN ('create_conversation', 'continue_conversation', 'decide_approval')",
        name="operation_valid",
    ),
    CheckConstraint(
        """
        (operation = 'create_conversation' AND command_scope = 'create')
        OR (
            operation = 'continue_conversation'
            AND command_scope = conversation_id::text
        )
        OR (
            operation = 'decide_approval'
            AND command_scope = approval_id::text
        )
        """,
        name="scope_matches_operation",
    ),
    CheckConstraint(
        """
        (
            operation IN ('create_conversation', 'continue_conversation')
            AND conversation_id IS NOT NULL
            AND message_id IS NOT NULL
            AND turn_id IS NOT NULL
            AND attempt_id IS NOT NULL
            AND approval_id IS NULL
            AND actor_id IS NULL
            AND approval_decision IS NULL
        )
        OR (
            operation = 'decide_approval'
            AND conversation_id IS NULL
            AND message_id IS NULL
            AND turn_id IS NULL
            AND attempt_id IS NULL
            AND approval_id IS NOT NULL
            AND actor_id IS NOT NULL
            AND approval_decision IN ('approve', 'reject')
        )
        """,
        name="result_matches_operation",
    ),
    CheckConstraint(
        "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
        name="idempotency_key_hash_valid",
    ),
    CheckConstraint(
        "request_fingerprint ~ '^[0-9a-f]{64}$'",
        name="request_fingerprint_valid",
    ),
)


execution_events = Table(
    "execution_events",
    metadata,
    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "turn_id",
        Uuid(as_uuid=True),
        ForeignKey(
            "turns.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column(
        "attempt_id",
        Uuid(as_uuid=True),
        nullable=True,
    ),
    Column(
        "sequence",
        Integer,
        nullable=False,
    ),
    Column(
        "kind",
        String(64),
        nullable=False,
    ),
    Column(
        "payload",
        JSONB,
        nullable=False,
    ),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    ForeignKeyConstraint(
        ["turn_id", "attempt_id"],
        ["turn_attempts.turn_id", "turn_attempts.id"],
        name="execution_event_attempt",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "turn_id",
        "sequence",
        name="turn_event_sequence",
    ),
    CheckConstraint(
        "sequence > 0",
        name="sequence_positive",
    ),
    CheckConstraint(
        """
        kind IN (
            'turn.started',
            'approval.required',
            'approval.resolved',
            'tool.started',
            'tool.completed',
            'tool.failed',
            'workflow.planned',
            'workflow.resumed',
            'workflow.terminal',
            'response.delta',
            'turn.completed',
            'turn.failed',
            'turn.cancelled'
        )
        """,
        name="kind_valid",
    ),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object'",
        name="payload_is_object",
    ),
)


tool_definitions = Table(
    "tool_definitions",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("team_id", Uuid(as_uuid=True), nullable=False),
    Column("tool_key", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("team_id", "tool_key", name="team_tool_key"),
    UniqueConstraint("team_id", "id", name="team_tool_identity"),
    CheckConstraint("tool_key ~ '^[a-z][a-z0-9._-]{0,99}$'", name="tool_key_valid"),
)


tool_versions = Table(
    "tool_versions",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column(
        "tool_definition_id",
        Uuid(as_uuid=True),
        ForeignKey("tool_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("version_number", Integer, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tool_definition_id", "version_number", name="tool_definition_version"),
    UniqueConstraint("tool_definition_id", "id", name="tool_definition_version_identity"),
    CheckConstraint("version_number > 0", name="version_number_positive"),
    CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest_is_object"),
    CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_valid"),
)


tool_invocations = Table(
    "tool_invocations",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("turn_id", Uuid(as_uuid=True), nullable=False),
    Column("attempt_id", Uuid(as_uuid=True), nullable=False),
    Column("invocation_number", Integer, nullable=False),
    Column("tool_definition_id", Uuid(as_uuid=True), nullable=False),
    Column("tool_version_id", Uuid(as_uuid=True), nullable=False),
    Column("arguments", JSONB, nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("authorized_scopes", JSONB, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("result", JSONB(none_as_null=True), nullable=True),
    Column("failure_code", String(100), nullable=True),
    ForeignKeyConstraint(
        ["turn_id", "attempt_id"],
        ["turn_attempts.turn_id", "turn_attempts.id"],
        name="tool_invocation_attempt",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tool_definition_id", "tool_version_id"],
        ["tool_versions.tool_definition_id", "tool_versions.id"],
        name="tool_invocation_tool_version",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("turn_id", "invocation_number", name="turn_invocation_number"),
    UniqueConstraint("idempotency_key", name="tool_invocation_idempotency_key"),
    UniqueConstraint(
        "id",
        "turn_id",
        "attempt_id",
        name="tool_invocation_workflow_identity",
    ),
    UniqueConstraint(
        "id",
        "turn_id",
        "attempt_id",
        "tool_definition_id",
        "tool_version_id",
        name="tool_invocation_policy_identity",
    ),
    CheckConstraint("invocation_number > 0", name="invocation_number_positive"),
    CheckConstraint("jsonb_typeof(arguments) = 'object'", name="arguments_is_object"),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9._:-]{1,200}$'",
        name="idempotency_key_valid",
    ),
    CheckConstraint(
        "jsonb_typeof(authorized_scopes) = 'array' "
        "AND jsonb_array_length(authorized_scopes) BETWEEN 1 AND 32",
        name="authorized_scopes_bounded_array",
    ),
    CheckConstraint(
        (
            "status IN ('pending', 'awaiting_confirmation', 'running', "
            "'succeeded', 'failed', 'unknown', 'cancelled')"
        ),
        name="status_valid",
    ),
    CheckConstraint(
        "result IS NULL OR jsonb_typeof(result) = 'object'",
        name="result_is_object",
    ),
    CheckConstraint(
        "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9._-]{0,99}$'",
        name="failure_code_valid",
    ),
    CheckConstraint(
        """
        (
            status IN ('pending', 'awaiting_confirmation')
            AND started_at IS NULL
            AND completed_at IS NULL
            AND result IS NULL
            AND failure_code IS NULL
        )
        OR (
            status = 'running'
            AND started_at IS NOT NULL
            AND completed_at IS NULL
            AND result IS NULL
            AND failure_code IS NULL
        )
        OR (
            status = 'succeeded'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND result IS NOT NULL
            AND failure_code IS NULL
        )
        OR (
            status IN ('failed', 'unknown')
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND result IS NULL
            AND failure_code IS NOT NULL
        )
        OR (
            status = 'cancelled'
            AND started_at IS NULL
            AND completed_at IS NOT NULL
            AND result IS NULL
            AND failure_code IS NULL
        )
        """,
        name="lifecycle_fields_match_status",
    ),
    CheckConstraint(
        "started_at IS NULL OR started_at >= created_at",
        name="started_at_not_before_created_at",
    ),
    CheckConstraint(
        "completed_at IS NULL OR (status = 'cancelled' AND completed_at >= created_at) "
        "OR (started_at IS NOT NULL AND completed_at >= started_at)",
        name="completed_at_not_before_started_at",
    ),
    Index("ix_tool_invocations_status", "status"),
)


policy_evaluations = Table(
    "policy_evaluations",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("team_id", Uuid(as_uuid=True), nullable=False),
    Column("requester_actor_id", Uuid(as_uuid=True), nullable=False),
    Column(
        "agent_version_id",
        Uuid(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("turn_id", Uuid(as_uuid=True), nullable=False),
    Column("attempt_id", Uuid(as_uuid=True), nullable=False),
    Column("invocation_id", Uuid(as_uuid=True), nullable=True),
    Column("tool_definition_id", Uuid(as_uuid=True), nullable=False),
    Column("tool_version_id", Uuid(as_uuid=True), nullable=False),
    Column("effect", String(32), nullable=False),
    Column("environment", String(32), nullable=False),
    Column("required_scopes", JSONB, nullable=False),
    Column("granted_scopes", JSONB, nullable=False),
    Column("policy_version", String(50), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("reason_code", String(100), nullable=False),
    Column("fingerprint_version", String(32), nullable=False),
    Column("fingerprint_digest", String(64), nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["team_id", "tool_definition_id"],
        ["tool_definitions.team_id", "tool_definitions.id"],
        name="policy_evaluation_team_tool",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["turn_id", "attempt_id"],
        ["turn_attempts.turn_id", "turn_attempts.id"],
        name="policy_evaluation_attempt",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tool_definition_id", "tool_version_id"],
        ["tool_versions.tool_definition_id", "tool_versions.id"],
        name="policy_evaluation_tool_version",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        [
            "invocation_id",
            "turn_id",
            "attempt_id",
            "tool_definition_id",
            "tool_version_id",
        ],
        [
            "tool_invocations.id",
            "tool_invocations.turn_id",
            "tool_invocations.attempt_id",
            "tool_invocations.tool_definition_id",
            "tool_invocations.tool_version_id",
        ],
        name="policy_evaluation_invocation",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "id",
        "team_id",
        "requester_actor_id",
        "invocation_id",
        "fingerprint_version",
        "fingerprint_digest",
        "tool_definition_id",
        "tool_version_id",
        "effect",
        name="policy_evaluation_approval_identity",
    ),
    CheckConstraint(
        "environment IN ('development', 'test', 'production')",
        name="environment_valid",
    ),
    CheckConstraint(
        "effect IN ('read_only', 'mutating', 'external_side_effect', 'privileged')",
        name="effect_valid",
    ),
    CheckConstraint(
        "jsonb_typeof(required_scopes) = 'array' "
        "AND jsonb_array_length(required_scopes) BETWEEN 1 AND 32",
        name="required_scopes_bounded_array",
    ),
    CheckConstraint(
        "jsonb_typeof(granted_scopes) = 'array' "
        "AND jsonb_array_length(granted_scopes) BETWEEN 0 AND 32",
        name="granted_scopes_bounded_array",
    ),
    CheckConstraint("policy_version = 'day8-v1'", name="policy_version_valid"),
    CheckConstraint(
        "decision IN ('allow', 'deny', 'require_confirmation', 'require_elevated_approval')",
        name="decision_valid",
    ),
    CheckConstraint(
        "(decision = 'allow' AND effect = 'read_only') "
        "OR (decision = 'require_confirmation' AND effect = 'mutating') "
        "OR decision IN ('deny', 'require_elevated_approval')",
        name="decision_matches_effect",
    ),
    CheckConstraint(
        "reason_code ~ '^[a-z][a-z0-9._-]{0,99}$'",
        name="reason_code_valid",
    ),
    CheckConstraint(
        "fingerprint_version = 'action-v1'",
        name="fingerprint_version_valid",
    ),
    CheckConstraint(
        "fingerprint_digest ~ '^[0-9a-f]{64}$'",
        name="fingerprint_digest_valid",
    ),
    Index("ix_policy_evaluations_invocation", "invocation_id", "evaluated_at"),
)


approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("team_id", Uuid(as_uuid=True), nullable=False),
    Column("policy_evaluation_id", Uuid(as_uuid=True), nullable=False),
    Column("invocation_id", Uuid(as_uuid=True), nullable=False),
    Column("requester_actor_id", Uuid(as_uuid=True), nullable=False),
    Column("fingerprint_version", String(32), nullable=False),
    Column("fingerprint_digest", String(64), nullable=False),
    Column("tool_definition_id", Uuid(as_uuid=True), nullable=False),
    Column("tool_version_id", Uuid(as_uuid=True), nullable=False),
    Column("effect", String(32), nullable=False),
    Column("argument_fields", JSONB, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resolved_by_actor_id", Uuid(as_uuid=True), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        [
            "policy_evaluation_id",
            "team_id",
            "requester_actor_id",
            "invocation_id",
            "fingerprint_version",
            "fingerprint_digest",
            "tool_definition_id",
            "tool_version_id",
            "effect",
        ],
        [
            "policy_evaluations.id",
            "policy_evaluations.team_id",
            "policy_evaluations.requester_actor_id",
            "policy_evaluations.invocation_id",
            "policy_evaluations.fingerprint_version",
            "policy_evaluations.fingerprint_digest",
            "policy_evaluations.tool_definition_id",
            "policy_evaluations.tool_version_id",
            "policy_evaluations.effect",
        ],
        name="approval_policy_evaluation",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "fingerprint_version = 'action-v1'",
        name="fingerprint_version_valid",
    ),
    CheckConstraint(
        "fingerprint_digest ~ '^[0-9a-f]{64}$'",
        name="fingerprint_digest_valid",
    ),
    CheckConstraint("effect = 'mutating'", name="effect_requires_confirmation"),
    CheckConstraint(
        "jsonb_typeof(argument_fields) = 'array'",
        name="argument_fields_is_array",
    ),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')",
        name="status_valid",
    ),
    CheckConstraint("expires_at > created_at", name="expires_at_after_created_at"),
    CheckConstraint(
        """
        (
            status = 'pending'
            AND resolved_by_actor_id IS NULL
            AND resolved_at IS NULL
            AND consumed_at IS NULL
        )
        OR (
            status IN ('approved', 'rejected')
            AND resolved_by_actor_id IS NOT NULL
            AND resolved_at IS NOT NULL
            AND resolved_at < expires_at
            AND consumed_at IS NULL
        )
        OR (
            status = 'expired'
            AND resolved_by_actor_id IS NULL
            AND resolved_at IS NOT NULL
            AND resolved_at >= expires_at
            AND consumed_at IS NULL
        )
        OR (
            status = 'consumed'
            AND resolved_by_actor_id IS NOT NULL
            AND resolved_at IS NOT NULL
            AND consumed_at IS NOT NULL
            AND resolved_at < expires_at
            AND consumed_at >= resolved_at
            AND consumed_at < expires_at
        )
        """,
        name="lifecycle_fields_match_status",
    ),
    Index("ix_approval_requests_team_status_expiry", "team_id", "status", "expires_at"),
    Index(
        "uq_approval_requests_active_invocation",
        "invocation_id",
        unique=True,
        postgresql_where=text("status IN ('pending', 'approved')"),
    ),
)


turn_workflows = Table(
    "turn_workflows",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("turn_id", Uuid(as_uuid=True), nullable=False),
    Column("attempt_id", Uuid(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("plan_version", Integer, nullable=False),
    Column("plan_fingerprint_version", String(32), nullable=True),
    Column("plan_fingerprint_digest", String(64), nullable=True),
    Column(
        "approval_id",
        Uuid(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column(
        "output_message_id",
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["turn_id", "attempt_id"],
        ["turn_attempts.turn_id", "turn_attempts.id"],
        name="workflow_attempt",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("turn_id", name="turn_workflow"),
    UniqueConstraint("approval_id", name="workflow_approval"),
    UniqueConstraint("output_message_id", name="workflow_output_message"),
    UniqueConstraint("id", "turn_id", "attempt_id", name="workflow_execution_identity"),
    CheckConstraint("plan_version = 1", name="plan_version_day_9"),
    CheckConstraint(
        "(plan_fingerprint_version IS NULL) = (plan_fingerprint_digest IS NULL)",
        name="plan_fingerprint_complete",
    ),
    CheckConstraint(
        "plan_fingerprint_version IS NULL OR plan_fingerprint_version = 'workflow-plan-v1'",
        name="plan_fingerprint_version_valid",
    ),
    CheckConstraint(
        "plan_fingerprint_digest IS NULL OR plan_fingerprint_digest ~ '^[0-9a-f]{64}$'",
        name="plan_fingerprint_digest_valid",
    ),
    CheckConstraint(
        "status IN ('discovery_pending', 'discovery_running', 'discovery_failed', 'planning', "
        "'awaiting_confirmation', 'running', 'completing', 'completed', "
        "'failed', 'review_required', 'cancelled')",
        name="status_valid",
    ),
    CheckConstraint(
        """
        (
            status IN ('discovery_pending', 'discovery_running', 'planning')
            AND plan_fingerprint_version IS NULL
            AND plan_fingerprint_digest IS NULL
            AND approval_id IS NULL
            AND output_message_id IS NULL
            AND completed_at IS NULL
        )
        OR (
            status = 'discovery_failed'
            AND plan_fingerprint_version IS NULL
            AND plan_fingerprint_digest IS NULL
            AND approval_id IS NULL
            AND output_message_id IS NULL
            AND completed_at IS NOT NULL
        )
        OR (
            status IN ('awaiting_confirmation', 'running')
            AND plan_fingerprint_version IS NOT NULL
            AND plan_fingerprint_digest IS NOT NULL
            AND approval_id IS NOT NULL
            AND output_message_id IS NULL
            AND completed_at IS NULL
        )
        OR (
            status = 'completing'
            AND plan_fingerprint_version IS NOT NULL
            AND plan_fingerprint_digest IS NOT NULL
            AND output_message_id IS NULL
            AND completed_at IS NULL
        )
        OR (
            status IN ('completed', 'failed', 'review_required')
            AND plan_fingerprint_version IS NOT NULL
            AND plan_fingerprint_digest IS NOT NULL
            AND output_message_id IS NOT NULL
            AND completed_at IS NOT NULL
        )
        OR (
            status = 'cancelled'
            AND plan_fingerprint_version IS NOT NULL
            AND plan_fingerprint_digest IS NOT NULL
            AND approval_id IS NOT NULL
            AND output_message_id IS NULL
            AND completed_at IS NOT NULL
        )
        """,
        name="lifecycle_fields_match_status",
    ),
    CheckConstraint("updated_at >= created_at", name="updated_at_not_before_created_at"),
    CheckConstraint(
        "completed_at IS NULL OR completed_at >= updated_at",
        name="completed_at_not_before_updated_at",
    ),
    Index("ix_turn_workflows_status", "status"),
)


workflow_plan_approvals = Table(
    "workflow_plan_approvals",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column(
        "workflow_id",
        Uuid(as_uuid=True),
        ForeignKey("turn_workflows.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("team_id", Uuid(as_uuid=True), nullable=False),
    Column("requester_actor_id", Uuid(as_uuid=True), nullable=False),
    Column("fingerprint_version", String(32), nullable=False),
    Column("fingerprint_digest", String(64), nullable=False),
    Column("safe_actions", JSONB, nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resolved_by_actor_id", Uuid(as_uuid=True), nullable=True),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("workflow_id", name="workflow_plan_approval_workflow"),
    CheckConstraint(
        "fingerprint_version = 'workflow-plan-v1'",
        name="fingerprint_version_supported",
    ),
    CheckConstraint(
        "fingerprint_digest ~ '^[0-9a-f]{64}$'",
        name="fingerprint_digest_valid",
    ),
    CheckConstraint(
        "jsonb_typeof(safe_actions) = 'array' AND jsonb_array_length(safe_actions) > 0",
        name="safe_actions_nonempty_array",
    ),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')",
        name="status_valid",
    ),
    CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    CheckConstraint(
        """
        (status = 'pending' AND resolved_by_actor_id IS NULL
            AND resolved_at IS NULL AND consumed_at IS NULL)
        OR (status IN ('approved', 'rejected') AND resolved_by_actor_id IS NOT NULL
            AND resolved_at IS NOT NULL AND resolved_at < expires_at
            AND consumed_at IS NULL)
        OR (status = 'expired' AND resolved_by_actor_id IS NULL
            AND resolved_at IS NOT NULL AND resolved_at >= expires_at
            AND consumed_at IS NULL)
        OR (status = 'consumed' AND resolved_by_actor_id IS NOT NULL
            AND resolved_at IS NOT NULL AND consumed_at IS NOT NULL
            AND resolved_at < expires_at AND consumed_at >= resolved_at
            AND consumed_at < expires_at)
        """,
        name="lifecycle_fields_match_status",
    ),
    Index("ix_workflow_plan_approvals_team_status_expiry", "team_id", "status", "expires_at"),
)


workflow_steps = Table(
    "workflow_steps",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("workflow_id", Uuid(as_uuid=True), nullable=False),
    Column("turn_id", Uuid(as_uuid=True), nullable=False),
    Column("attempt_id", Uuid(as_uuid=True), nullable=False),
    Column("step_number", Integer, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("predecessor_step_id", Uuid(as_uuid=True), nullable=True),
    Column("predecessor_step_number", Integer, nullable=True),
    Column("invocation_id", Uuid(as_uuid=True), nullable=True),
    Column(
        "output_message_id",
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("failure_code", String(100), nullable=True),
    ForeignKeyConstraint(
        ["workflow_id", "turn_id", "attempt_id"],
        ["turn_workflows.id", "turn_workflows.turn_id", "turn_workflows.attempt_id"],
        name="workflow_step_execution",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["invocation_id", "turn_id", "attempt_id"],
        ["tool_invocations.id", "tool_invocations.turn_id", "tool_invocations.attempt_id"],
        name="workflow_step_invocation",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["workflow_id", "predecessor_step_number", "predecessor_step_id"],
        ["workflow_steps.workflow_id", "workflow_steps.step_number", "workflow_steps.id"],
        name="workflow_step_predecessor",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("workflow_id", "step_number", name="workflow_step_number"),
    UniqueConstraint("workflow_id", "step_number", "id", name="workflow_step_identity"),
    UniqueConstraint("invocation_id", name="workflow_step_invocation_identity"),
    UniqueConstraint("output_message_id", name="workflow_step_output_message"),
    CheckConstraint("step_number > 0", name="step_number_positive"),
    CheckConstraint(
        """
        (step_number = 1 AND predecessor_step_id IS NULL
            AND predecessor_step_number IS NULL)
        OR (step_number > 1 AND predecessor_step_id IS NOT NULL
            AND predecessor_step_number = step_number - 1)
        """,
        name="immediate_predecessor",
    ),
    CheckConstraint(
        "kind IN ('discovery_tool', 'mutation_tool', 'final_response')",
        name="kind_valid",
    ),
    CheckConstraint(
        "(kind IN ('discovery_tool', 'mutation_tool') AND invocation_id IS NOT NULL "
        "AND output_message_id IS NULL) "
        "OR (kind = 'final_response' AND invocation_id IS NULL)",
        name="references_match_kind",
    ),
    CheckConstraint(
        "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'skipped')",
        name="status_valid",
    ),
    CheckConstraint(
        "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9._-]{0,99}$'",
        name="failure_code_valid",
    ),
    CheckConstraint(
        """
        (status = 'pending' AND started_at IS NULL AND completed_at IS NULL
            AND output_message_id IS NULL AND failure_code IS NULL)
        OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL
            AND output_message_id IS NULL AND failure_code IS NULL)
        OR (status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND failure_code IS NULL
            AND (kind <> 'final_response' OR output_message_id IS NOT NULL))
        OR (status IN ('failed', 'unknown') AND started_at IS NOT NULL
            AND completed_at IS NOT NULL AND output_message_id IS NULL
            AND failure_code IS NOT NULL)
        OR (status = 'skipped' AND started_at IS NULL AND completed_at IS NOT NULL
            AND output_message_id IS NULL AND failure_code IS NOT NULL)
        """,
        name="lifecycle_fields_match_status",
    ),
    CheckConstraint(
        "started_at IS NULL OR started_at >= created_at",
        name="started_at_not_before_created_at",
    ),
    CheckConstraint(
        "completed_at IS NULL OR completed_at >= COALESCE(started_at, created_at)",
        name="completed_at_not_before_start",
    ),
    Index("ix_workflow_steps_workflow_status", "workflow_id", "status"),
)


tool_conformance_runs = Table(
    "tool_conformance_runs",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column(
        "tool_version_id",
        Uuid(as_uuid=True),
        ForeignKey("tool_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tool_version_id", "id", name="tool_version_conformance_identity"),
    CheckConstraint("status IN ('passed', 'failed')", name="status_valid"),
    CheckConstraint("completed_at >= started_at", name="completed_at_not_before_started_at"),
)


tool_conformance_case_results = Table(
    "tool_conformance_case_results",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column(
        "run_id",
        Uuid(as_uuid=True),
        ForeignKey("tool_conformance_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("case_key", String(100), nullable=False),
    Column("status", String(32), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("diagnostic_code", String(200), nullable=True),
    UniqueConstraint("run_id", "case_key", name="conformance_run_case"),
    CheckConstraint("case_key ~ '^[a-z][a-z0-9._-]{0,99}$'", name="case_key_valid"),
    CheckConstraint("status IN ('passed', 'failed')", name="status_valid"),
    CheckConstraint("duration_ms BETWEEN 0 AND 300000", name="duration_ms_bounded"),
    CheckConstraint(
        """
        (status = 'passed' AND diagnostic_code IS NULL)
        OR
        (
            status = 'failed'
            AND diagnostic_code ~ '^[a-z][a-z0-9._-]{0,199}$'
        )
        """,
        name="diagnostic_matches_status",
    ),
)


tool_version_states = Table(
    "tool_version_states",
    metadata,
    Column("tool_version_id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column("status", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("activated_conformance_run_id", Uuid(as_uuid=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tool_version_id"],
        ["tool_versions.id"],
        name="tool_version_state_version",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tool_version_id", "activated_conformance_run_id"],
        ["tool_conformance_runs.tool_version_id", "tool_conformance_runs.id"],
        name="tool_version_state_activation_run",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "status IN ('draft', 'active', 'deprecated', 'disabled')",
        name="status_valid",
    ),
    CheckConstraint("revision > 0", name="revision_positive"),
    CheckConstraint("updated_at >= created_at", name="updated_at_not_before_created_at"),
    CheckConstraint(
        """
        (status = 'draft' AND activated_conformance_run_id IS NULL)
        OR status = 'disabled'
        OR (
            status IN ('active', 'deprecated')
            AND activated_conformance_run_id IS NOT NULL
        )
        """,
        name="activation_matches_status",
    ),
)


agent_tool_bindings = Table(
    "agent_tool_bindings",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, nullable=False),
    Column(
        "agent_version_id",
        Uuid(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("tool_definition_id", Uuid(as_uuid=True), nullable=False),
    Column("tool_version_id", Uuid(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tool_definition_id", "tool_version_id"],
        ["tool_versions.tool_definition_id", "tool_versions.id"],
        name="agent_binding_tool_version",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("agent_version_id", "tool_definition_id", name="agent_stable_tool"),
)


Index(
    "ix_agent_definitions_team_id",
    agent_definitions.c.team_id,
)

Index(
    "ix_conversations_team_status",
    conversations.c.team_id,
    conversations.c.status,
)

Index(
    "ix_turns_conversation_status",
    turns.c.conversation_id,
    turns.c.status,
)

Index(
    "ix_conversation_summaries_compatible",
    conversation_summaries.c.conversation_id,
    conversation_summaries.c.agent_version_id,
    conversation_summaries.c.through_sequence,
)

Index(
    "ix_turn_attempts_turn_status",
    turn_attempts.c.turn_id,
    turn_attempts.c.status,
)

Index(
    "uq_execution_events_one_terminal_per_turn",
    execution_events.c.turn_id,
    unique=True,
    postgresql_where=text("kind IN ('turn.completed', 'turn.failed')"),
)

Index(
    "uq_execution_events_one_started_per_turn",
    execution_events.c.turn_id,
    unique=True,
    postgresql_where=text("kind = 'turn.started'"),
)

Index(
    "ix_tool_versions_definition_created",
    tool_versions.c.tool_definition_id,
    tool_versions.c.created_at,
)

Index(
    "ix_tool_version_states_eligible",
    tool_version_states.c.status,
    tool_version_states.c.tool_version_id,
)

Index(
    "ix_agent_tool_bindings_agent_version",
    agent_tool_bindings.c.agent_version_id,
    agent_tool_bindings.c.tool_version_id,
)

Index(
    "ix_tool_conformance_runs_version_completed",
    tool_conformance_runs.c.tool_version_id,
    tool_conformance_runs.c.completed_at,
)
