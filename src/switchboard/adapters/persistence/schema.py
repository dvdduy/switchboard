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
        ("status IN ('received', 'running', 'completed', 'failed', 'cancelled')"),
        name="status_valid",
    ),
    CheckConstraint(
        """
        (
            status IN ('received', 'running')
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
        ("status IN ('pending', 'running', 'succeeded', 'failed')"),
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
            status = 'running'
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
            'response.delta',
            'turn.completed',
            'turn.failed'
        )
        """,
        name="kind_valid",
    ),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object'",
        name="payload_is_object",
    ),
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
