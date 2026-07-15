"""add unknown invocation outcome

Revision ID: f3c4d5e6f7a8
Revises: e2b3c4d5e6f7
Create Date: 2026-07-14 22:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3c4d5e6f7a8"
down_revision: str | None = "e2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def upgrade() -> None:
    """Represent an ambiguous post-dispatch outcome without claiming failure."""
    _replace_check(
        "tool_invocations",
        "status_valid",
        "status IN ('pending', 'awaiting_confirmation', 'running', "
        "'succeeded', 'failed', 'unknown', 'cancelled')",
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
        OR (status IN ('failed', 'unknown') AND started_at IS NOT NULL
            AND completed_at IS NOT NULL AND result IS NULL AND failure_code IS NOT NULL)
        OR (status = 'cancelled' AND started_at IS NULL AND completed_at IS NOT NULL
            AND result IS NULL AND failure_code IS NULL)
        """,
    )


def downgrade() -> None:
    """Collapse unknown evidence to failed for compatibility with the prior schema."""
    op.execute("UPDATE tool_invocations SET status = 'failed' WHERE status = 'unknown'")
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
        "status_valid",
        "status IN ('pending', 'awaiting_confirmation', 'running', "
        "'succeeded', 'failed', 'cancelled')",
    )
