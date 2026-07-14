"""enforce unique turn start event

Revision ID: 443feebc380e
Revises: adfa099fa157
Create Date: 2026-07-13 17:52:36.130093

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "443feebc380e"
down_revision: str | Sequence[str] | None = "adfa099fa157"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_index(
        "uq_execution_events_one_started_per_turn",
        "execution_events",
        ["turn_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'turn.started'"),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(
        "uq_execution_events_one_started_per_turn",
        table_name="execution_events",
        if_exists=True,
    )
