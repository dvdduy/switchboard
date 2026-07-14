"""add context policy and conversation summaries

Revision ID: b48f1c2d9a70
Revises: 443feebc380e
Create Date: 2026-07-13 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b48f1c2d9a70"
down_revision: str | Sequence[str] | None = "443feebc380e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist immutable context policies and reusable prefix summaries."""

    policy_columns = (
        ("model_window_tokens", "4096"),
        ("reserved_output_tokens", "512"),
        ("fixed_overhead_tokens", "256"),
        ("summary_max_tokens", "256"),
        ("minimum_recent_messages", "1"),
    )
    for name, default in policy_columns:
        op.add_column(
            "agent_versions",
            sa.Column(
                name,
                sa.Integer(),
                nullable=False,
                server_default=sa.text(default),
            ),
        )
        op.alter_column("agent_versions", name, server_default=None)

    op.create_check_constraint(
        op.f("ck_agent_versions_context_policy_fields_positive"),
        "agent_versions",
        """
        model_window_tokens > 0
        AND reserved_output_tokens > 0
        AND fixed_overhead_tokens > 0
        AND summary_max_tokens > 0
        AND minimum_recent_messages > 0
        """,
    )
    op.create_check_constraint(
        op.f("ck_agent_versions_context_policy_has_input_capacity"),
        "agent_versions",
        "reserved_output_tokens + fixed_overhead_tokens < model_window_tokens",
    )
    op.create_check_constraint(
        op.f("ck_agent_versions_summary_fits_input_capacity"),
        "agent_versions",
        """
        summary_max_tokens
        < model_window_tokens - reserved_output_tokens - fixed_overhead_tokens
        """,
    )

    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False),
        sa.Column("from_sequence", sa.Integer(), nullable=False),
        sa.Column("through_sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("summarizer_version", sa.String(length=100), nullable=False),
        sa.Column("token_counter_version", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_sequence = 1",
            name=op.f("ck_conversation_summaries_coverage_starts_at_one"),
        ),
        sa.CheckConstraint(
            "through_sequence >= from_sequence",
            name=op.f("ck_conversation_summaries_coverage_ordered"),
        ),
        sa.CheckConstraint(
            "estimated_token_count > 0",
            name=op.f("ck_conversation_summaries_estimated_token_count_positive"),
        ),
        sa.CheckConstraint(
            "btrim(content) <> ''",
            name=op.f("ck_conversation_summaries_content_not_blank"),
        ),
        sa.CheckConstraint(
            "btrim(summarizer_version) <> ''",
            name=op.f("ck_conversation_summaries_summarizer_version_not_blank"),
        ),
        sa.CheckConstraint(
            "btrim(token_counter_version) <> ''",
            name=op.f("ck_conversation_summaries_token_counter_version_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "from_sequence"],
            ["messages.conversation_id", "messages.sequence"],
            name="summary_from_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "through_sequence"],
            ["messages.conversation_id", "messages.sequence"],
            name="summary_through_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            name="summary_agent_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_summaries")),
        sa.UniqueConstraint(
            "conversation_id",
            "agent_version_id",
            "from_sequence",
            "through_sequence",
            "summarizer_version",
            "token_counter_version",
            name="conversation_summary_authority",
        ),
    )
    op.create_index(
        "ix_conversation_summaries_compatible",
        "conversation_summaries",
        ["conversation_id", "agent_version_id", "through_sequence"],
    )


def downgrade() -> None:
    """Remove conversation-summary persistence and version policies."""

    op.drop_index(
        "ix_conversation_summaries_compatible",
        table_name="conversation_summaries",
    )
    op.drop_table("conversation_summaries")

    op.drop_constraint(
        op.f("ck_agent_versions_summary_fits_input_capacity"),
        "agent_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_versions_context_policy_has_input_capacity"),
        "agent_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_versions_context_policy_fields_positive"),
        "agent_versions",
        type_="check",
    )
    for name in (
        "minimum_recent_messages",
        "summary_max_tokens",
        "fixed_overhead_tokens",
        "reserved_output_tokens",
        "model_window_tokens",
    ):
        op.drop_column("agent_versions", name)
