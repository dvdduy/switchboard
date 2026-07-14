"""add awaiting confirmation state

Revision ID: b9e0f1a2b3c4
Revises: b8d9e0f1a2b3
Create Date: 2026-07-15 03:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b9e0f1a2b3c4"
down_revision: str | None = "b8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    _replace_check(
        "turns",
        "status_valid",
        "status IN ('received', 'running', 'awaiting_confirmation', "
        "'completed', 'failed', 'cancelled')",
    )
    _replace_check(
        "turns",
        "completion_matches_status",
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
    )
    _replace_check(
        "turn_attempts",
        "status_valid",
        "status IN ('pending', 'running', 'awaiting_confirmation', 'succeeded', 'failed')",
    )
    _replace_check(
        "turn_attempts",
        "lifecycle_fields_match_status",
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
        """,
    )
    _replace_check(
        "tool_invocations",
        "status_valid",
        "status IN ('pending', 'awaiting_confirmation', 'running', 'succeeded', 'failed')",
    )
    _replace_check(
        "tool_invocations",
        "lifecycle_fields_match_status",
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
            status = 'failed'
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND result IS NULL
            AND failure_code IS NOT NULL
        )
        """,
    )
    _replace_check(
        "execution_events",
        "kind_valid",
        """
        kind IN (
            'turn.started',
            'approval.required',
            'tool.started',
            'tool.completed',
            'tool.failed',
            'response.delta',
            'turn.completed',
            'turn.failed'
        )
        """,
    )


def downgrade() -> None:
    """Downgrade schema."""
    _replace_check(
        "execution_events",
        "kind_valid",
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
    _replace_check(
        "tool_invocations",
        "lifecycle_fields_match_status",
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
    )
    _replace_check(
        "tool_invocations",
        "status_valid",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    _replace_check(
        "turn_attempts",
        "lifecycle_fields_match_status",
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
    )
    _replace_check(
        "turn_attempts",
        "status_valid",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    _replace_check(
        "turns",
        "completion_matches_status",
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
    )
    _replace_check(
        "turns",
        "status_valid",
        "status IN ('received', 'running', 'completed', 'failed', 'cancelled')",
    )


def _replace_check(table: str, suffix: str, condition: str) -> None:
    name = op.f(f"ck_{table}_{suffix}")
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)
