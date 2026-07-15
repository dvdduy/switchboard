"""add safe workflow execution events

Revision ID: g4d5e6f7a8b9
Revises: f3c4d5e6f7a8
Create Date: 2026-07-14 23:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "g4d5e6f7a8b9"
down_revision: str | None = "f3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def upgrade() -> None:
    """Allow additive, redacted workflow lifecycle observations."""
    _replace_check(
        "execution_events",
        "kind_valid",
        """
        kind IN ('turn.started', 'approval.required', 'approval.resolved',
            'tool.started', 'tool.completed', 'tool.failed',
            'workflow.planned', 'workflow.resumed', 'workflow.terminal',
            'response.delta', 'turn.completed', 'turn.failed', 'turn.cancelled')
        """,
    )


def downgrade() -> None:
    """Remove workflow observations before restoring the prior constraint."""
    op.execute("DELETE FROM execution_events WHERE kind LIKE 'workflow.%'")
    _replace_check(
        "execution_events",
        "kind_valid",
        """
        kind IN ('turn.started', 'approval.required', 'approval.resolved',
            'tool.started', 'tool.completed', 'tool.failed', 'response.delta',
            'turn.completed', 'turn.failed', 'turn.cancelled')
        """,
    )
