"""add workflow plan approvals

Revision ID: e2b3c4d5e6f7
Revises: d1a2b3c4d5e6
Create Date: 2026-07-14 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2b3c4d5e6f7"
down_revision: str | None = "d1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add exact-plan approval storage without changing Day 8 approvals."""
    op.drop_constraint(
        op.f("fk_turn_workflows_approval_id_approval_requests"),
        "turn_workflows",
        type_="foreignkey",
    )
    op.create_table(
        "workflow_plan_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("requester_actor_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint_version", sa.String(32), nullable=False),
        sa.Column("fingerprint_digest", sa.String(64), nullable=False),
        sa.Column("safe_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["turn_workflows.id"],
            name=op.f("fk_workflow_plan_approvals_workflow_id_turn_workflows"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_plan_approvals")),
        sa.UniqueConstraint("workflow_id", name="workflow_plan_approval_workflow"),
        sa.CheckConstraint(
            "fingerprint_version = 'workflow-plan-v1'",
            name=op.f("ck_workflow_plan_approvals_fingerprint_version_supported"),
        ),
        sa.CheckConstraint(
            "fingerprint_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_workflow_plan_approvals_fingerprint_digest_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(safe_actions) = 'array' AND jsonb_array_length(safe_actions) > 0",
            name=op.f("ck_workflow_plan_approvals_safe_actions_nonempty_array"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')",
            name=op.f("ck_workflow_plan_approvals_status_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_workflow_plan_approvals_expiry_after_creation"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_workflow_plan_approvals_lifecycle_fields_match_status"),
        ),
    )
    op.create_index(
        "ix_workflow_plan_approvals_team_status_expiry",
        "workflow_plan_approvals",
        ["team_id", "status", "expires_at"],
    )
    op.create_foreign_key(
        "fk_turn_workflows_approval_id_workflow_plan_approvals",
        "turn_workflows",
        "workflow_plan_approvals",
        ["approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Restore the Day 8 invocation-approval link used by the prior schema."""
    op.drop_constraint(
        "fk_turn_workflows_approval_id_workflow_plan_approvals",
        "turn_workflows",
        type_="foreignkey",
    )
    op.execute(
        """
        UPDATE turn_workflows
        SET status = 'planning',
            plan_fingerprint_version = NULL,
            plan_fingerprint_digest = NULL,
            approval_id = NULL,
            completed_at = NULL
        WHERE approval_id IS NOT NULL
        """
    )
    op.drop_index(
        "ix_workflow_plan_approvals_team_status_expiry",
        table_name="workflow_plan_approvals",
    )
    op.drop_table("workflow_plan_approvals")
    op.create_foreign_key(
        op.f("fk_turn_workflows_approval_id_approval_requests"),
        "turn_workflows",
        "approval_requests",
        ["approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )
