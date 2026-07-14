"""add approval decisions and cancellation

Revision ID: c0f1a2b3c4d5
Revises: b9e0f1a2b3c4
Create Date: 2026-07-15 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0f1a2b3c4d5"
down_revision: str | None = "b9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("command_receipts", "conversation_id", nullable=True)
    op.alter_column("command_receipts", "message_id", nullable=True)
    op.alter_column("command_receipts", "turn_id", nullable=True)
    op.alter_column("command_receipts", "attempt_id", nullable=True)
    op.add_column("command_receipts", sa.Column("approval_id", sa.Uuid(), nullable=True))
    op.add_column("command_receipts", sa.Column("actor_id", sa.Uuid(), nullable=True))
    op.add_column("command_receipts", sa.Column("approval_decision", sa.String(16), nullable=True))
    op.create_foreign_key(
        op.f("fk_command_receipts_approval_id_approval_requests"),
        "command_receipts",
        "approval_requests",
        ["approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _replace_check(
        "command_receipts",
        "operation_valid",
        "operation IN ('create_conversation', 'continue_conversation', 'decide_approval')",
    )
    _replace_check(
        "command_receipts",
        "scope_matches_operation",
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
    )
    op.create_check_constraint(
        op.f("ck_command_receipts_result_matches_operation"),
        "command_receipts",
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
    )
    _replace_check(
        "turn_attempts",
        "status_valid",
        "status IN ('pending', 'running', 'awaiting_confirmation', "
        "'succeeded', 'failed', 'cancelled')",
    )
    _replace_check(
        "turn_attempts",
        "lifecycle_fields_match_status",
        """
        (status = 'pending' AND started_at IS NULL AND completed_at IS NULL
            AND failure_code IS NULL)
        OR (status IN ('running', 'awaiting_confirmation') AND started_at IS NOT NULL
            AND completed_at IS NULL AND failure_code IS NULL)
        OR (status = 'succeeded' AND started_at IS NOT NULL
            AND completed_at IS NOT NULL AND failure_code IS NULL)
        OR (status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND failure_code IS NOT NULL AND btrim(failure_code) <> '')
        OR (status = 'cancelled' AND started_at IS NOT NULL
            AND completed_at IS NOT NULL AND failure_code IS NULL)
        """,
    )
    _replace_check(
        "tool_invocations",
        "status_valid",
        "status IN ('pending', 'awaiting_confirmation', 'running', "
        "'succeeded', 'failed', 'cancelled')",
    )
    _replace_check(
        "tool_invocations",
        "lifecycle_fields_match_status",
        """
        (status IN ('pending', 'awaiting_confirmation') AND started_at IS NULL
            AND completed_at IS NULL AND result IS NULL AND failure_code IS NULL)
        OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL
            AND result IS NULL AND failure_code IS NULL)
        OR (status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND result IS NOT NULL AND failure_code IS NULL)
        OR (status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND result IS NULL AND failure_code IS NOT NULL)
        OR (status = 'cancelled' AND started_at IS NULL AND completed_at IS NOT NULL
            AND result IS NULL AND failure_code IS NULL)
        """,
    )
    _replace_check(
        "tool_invocations",
        "completed_at_not_before_started_at",
        "completed_at IS NULL OR (status = 'cancelled' AND completed_at >= created_at) "
        "OR (started_at IS NOT NULL AND completed_at >= started_at)",
    )
    _replace_check(
        "execution_events",
        "kind_valid",
        """
        kind IN ('turn.started', 'approval.required', 'approval.resolved',
            'tool.started', 'tool.completed', 'tool.failed', 'response.delta',
            'turn.completed', 'turn.failed', 'turn.cancelled')
        """,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM command_receipts WHERE operation = 'decide_approval'")
    _replace_check(
        "execution_events",
        "kind_valid",
        """
        kind IN ('turn.started', 'approval.required', 'tool.started', 'tool.completed',
            'tool.failed', 'response.delta', 'turn.completed', 'turn.failed')
        """,
    )
    _replace_check(
        "tool_invocations",
        "completed_at_not_before_started_at",
        "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
    )
    _replace_check(
        "tool_invocations",
        "lifecycle_fields_match_status",
        """
        (status = 'pending' AND started_at IS NULL AND completed_at IS NULL
            AND result IS NULL AND failure_code IS NULL)
        OR (status = 'awaiting_confirmation' AND started_at IS NULL
            AND completed_at IS NULL AND result IS NULL AND failure_code IS NULL)
        OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL
            AND result IS NULL AND failure_code IS NULL)
        OR (status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND result IS NOT NULL AND failure_code IS NULL)
        OR (status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND result IS NULL AND failure_code IS NOT NULL)
        """,
    )
    _replace_check(
        "tool_invocations",
        "status_valid",
        "status IN ('pending', 'awaiting_confirmation', 'running', 'succeeded', 'failed')",
    )
    _replace_check(
        "turn_attempts",
        "lifecycle_fields_match_status",
        """
        (status = 'pending' AND started_at IS NULL AND completed_at IS NULL
            AND failure_code IS NULL)
        OR (status IN ('running', 'awaiting_confirmation') AND started_at IS NOT NULL
            AND completed_at IS NULL AND failure_code IS NULL)
        OR (status = 'succeeded' AND started_at IS NOT NULL
            AND completed_at IS NOT NULL AND failure_code IS NULL)
        OR (status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND failure_code IS NOT NULL AND btrim(failure_code) <> '')
        """,
    )
    _replace_check(
        "turn_attempts",
        "status_valid",
        "status IN ('pending', 'running', 'awaiting_confirmation', 'succeeded', 'failed')",
    )
    op.drop_constraint(
        op.f("ck_command_receipts_result_matches_operation"),
        "command_receipts",
        type_="check",
    )
    _replace_check(
        "command_receipts",
        "scope_matches_operation",
        """
        (operation = 'create_conversation' AND command_scope = 'create')
        OR (operation = 'continue_conversation' AND command_scope = conversation_id::text)
        """,
    )
    _replace_check(
        "command_receipts",
        "operation_valid",
        "operation IN ('create_conversation', 'continue_conversation')",
    )
    op.drop_constraint(
        op.f("fk_command_receipts_approval_id_approval_requests"),
        "command_receipts",
        type_="foreignkey",
    )
    op.drop_column("command_receipts", "approval_decision")
    op.drop_column("command_receipts", "actor_id")
    op.drop_column("command_receipts", "approval_id")
    op.alter_column("command_receipts", "attempt_id", nullable=False)
    op.alter_column("command_receipts", "turn_id", nullable=False)
    op.alter_column("command_receipts", "message_id", nullable=False)
    op.alter_column("command_receipts", "conversation_id", nullable=False)


def _replace_check(table: str, suffix: str, condition: str) -> None:
    name = op.f(f"ck_{table}_{suffix}")
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)
