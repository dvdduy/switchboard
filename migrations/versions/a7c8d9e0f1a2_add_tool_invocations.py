"""add tool invocations

Revision ID: a7c8d9e0f1a2
Revises: f2a6b7c8d9e0
Create Date: 2026-07-14 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f2a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f("ck_execution_events_kind_valid"),
        "execution_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_execution_events_kind_valid"),
        "execution_events",
        """
        kind IN (
            'turn.started',
            'tool.started',
            'tool.completed',
            'tool.failed',
            'response.delta',
            'turn.completed',
            'turn.failed'
        )
        """,
    )
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("invocation_number", sa.Integer(), nullable=False),
        sa.Column("tool_definition_id", sa.Uuid(), nullable=False),
        sa.Column("tool_version_id", sa.Uuid(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("authorized_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(arguments) = 'object'",
            name=op.f("ck_tool_invocations_arguments_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_scopes) = 'array' "
            "AND jsonb_array_length(authorized_scopes) BETWEEN 1 AND 32",
            name=op.f("ck_tool_invocations_authorized_scopes_bounded_array"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name=op.f("ck_tool_invocations_completed_at_not_before_started_at"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name=op.f("ck_tool_invocations_failure_code_valid"),
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9._:-]{1,200}$'",
            name=op.f("ck_tool_invocations_idempotency_key_valid"),
        ),
        sa.CheckConstraint(
            "invocation_number = 1",
            name=op.f("ck_tool_invocations_invocation_number_day_7"),
        ),
        sa.CheckConstraint(
            """
            (
                status = 'pending'
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
                status = 'failed'
                AND started_at IS NOT NULL
                AND completed_at IS NOT NULL
                AND result IS NULL
                AND failure_code IS NOT NULL
            )
            """,
            name=op.f("ck_tool_invocations_lifecycle_fields_match_status"),
        ),
        sa.CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'",
            name=op.f("ck_tool_invocations_result_is_object"),
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name=op.f("ck_tool_invocations_started_at_not_before_created_at"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name=op.f("ck_tool_invocations_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "attempt_id"],
            ["turn_attempts.turn_id", "turn_attempts.id"],
            name=op.f("fk_tool_invocations_turn_id_turn_attempts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id", "tool_version_id"],
            ["tool_versions.tool_definition_id", "tool_versions.id"],
            name=op.f("fk_tool_invocations_tool_definition_id_tool_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_invocations")),
        sa.UniqueConstraint("attempt_id", name="attempt_tool_invocation"),
        sa.UniqueConstraint("idempotency_key", name="tool_invocation_idempotency_key"),
        sa.UniqueConstraint("turn_id", "invocation_number", name="turn_invocation_number"),
    )
    op.create_index(
        "ix_tool_invocations_status",
        "tool_invocations",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_tool_invocations_status", table_name="tool_invocations")
    op.drop_table("tool_invocations")
    op.drop_constraint(
        op.f("ck_execution_events_kind_valid"),
        "execution_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_execution_events_kind_valid"),
        "execution_events",
        """
        kind IN (
            'turn.started',
            'response.delta',
            'turn.completed',
            'turn.failed'
        )
        """,
    )
