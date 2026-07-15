"""add durable turn workflows

Revision ID: d1a2b3c4d5e6
Revises: c0f1a2b3c4d5
Create Date: 2026-07-14 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1a2b3c4d5e6"
down_revision: str | None = "c0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("attempt_tool_invocation", "tool_invocations", type_="unique")
    op.drop_constraint(
        op.f("ck_tool_invocations_invocation_number_day_7"),
        "tool_invocations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_tool_invocations_invocation_number_positive"),
        "tool_invocations",
        "invocation_number > 0",
    )
    op.create_unique_constraint(
        "tool_invocation_workflow_identity",
        "tool_invocations",
        ["id", "turn_id", "attempt_id"],
    )

    op.create_table(
        "turn_workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("plan_fingerprint_version", sa.String(32), nullable=True),
        sa.Column("plan_fingerprint_digest", sa.String(64), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("output_message_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["turn_id", "attempt_id"],
            ["turn_attempts.turn_id", "turn_attempts.id"],
            name="workflow_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval_requests.id"],
            name=op.f("fk_turn_workflows_approval_id_approval_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["output_message_id"],
            ["messages.id"],
            name=op.f("fk_turn_workflows_output_message_id_messages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turn_workflows")),
        sa.UniqueConstraint("turn_id", name="turn_workflow"),
        sa.UniqueConstraint("approval_id", name="workflow_approval"),
        sa.UniqueConstraint("output_message_id", name="workflow_output_message"),
        sa.UniqueConstraint(
            "id",
            "turn_id",
            "attempt_id",
            name="workflow_execution_identity",
        ),
        sa.CheckConstraint(
            "plan_version = 1",
            name=op.f("ck_turn_workflows_plan_version_day_9"),
        ),
        sa.CheckConstraint(
            "(plan_fingerprint_version IS NULL) = (plan_fingerprint_digest IS NULL)",
            name=op.f("ck_turn_workflows_plan_fingerprint_complete"),
        ),
        sa.CheckConstraint(
            "plan_fingerprint_version IS NULL OR plan_fingerprint_version = 'workflow-plan-v1'",
            name=op.f("ck_turn_workflows_plan_fingerprint_version_valid"),
        ),
        sa.CheckConstraint(
            "plan_fingerprint_digest IS NULL OR plan_fingerprint_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_turn_workflows_plan_fingerprint_digest_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('discovery_pending', 'discovery_running', 'discovery_failed', "
            "'planning', "
            "'awaiting_confirmation', 'running', 'completing', 'completed', "
            "'failed', 'review_required', 'cancelled')",
            name=op.f("ck_turn_workflows_status_valid"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_turn_workflows_lifecycle_fields_match_status"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_turn_workflows_updated_at_not_before_created_at"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= updated_at",
            name=op.f("ck_turn_workflows_completed_at_not_before_updated_at"),
        ),
    )
    op.create_index("ix_turn_workflows_status", "turn_workflows", ["status"])

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("predecessor_step_id", sa.Uuid(), nullable=True),
        sa.Column("predecessor_step_number", sa.Integer(), nullable=True),
        sa.Column("invocation_id", sa.Uuid(), nullable=True),
        sa.Column("output_message_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id", "turn_id", "attempt_id"],
            ["turn_workflows.id", "turn_workflows.turn_id", "turn_workflows.attempt_id"],
            name="workflow_step_execution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id", "turn_id", "attempt_id"],
            ["tool_invocations.id", "tool_invocations.turn_id", "tool_invocations.attempt_id"],
            name="workflow_step_invocation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "predecessor_step_number", "predecessor_step_id"],
            ["workflow_steps.workflow_id", "workflow_steps.step_number", "workflow_steps.id"],
            name="workflow_step_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["output_message_id"],
            ["messages.id"],
            name=op.f("fk_workflow_steps_output_message_id_messages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_steps")),
        sa.UniqueConstraint("workflow_id", "step_number", name="workflow_step_number"),
        sa.UniqueConstraint(
            "workflow_id",
            "step_number",
            "id",
            name="workflow_step_identity",
        ),
        sa.UniqueConstraint("invocation_id", name="workflow_step_invocation_identity"),
        sa.UniqueConstraint("output_message_id", name="workflow_step_output_message"),
        sa.CheckConstraint(
            "step_number > 0",
            name=op.f("ck_workflow_steps_step_number_positive"),
        ),
        sa.CheckConstraint(
            """
            (step_number = 1 AND predecessor_step_id IS NULL
                AND predecessor_step_number IS NULL)
            OR (step_number > 1 AND predecessor_step_id IS NOT NULL
                AND predecessor_step_number = step_number - 1)
            """,
            name=op.f("ck_workflow_steps_immediate_predecessor"),
        ),
        sa.CheckConstraint(
            "kind IN ('discovery_tool', 'mutation_tool', 'final_response')",
            name=op.f("ck_workflow_steps_kind_valid"),
        ),
        sa.CheckConstraint(
            "(kind IN ('discovery_tool', 'mutation_tool') AND invocation_id IS NOT NULL "
            "AND output_message_id IS NULL) "
            "OR (kind = 'final_response' AND invocation_id IS NULL)",
            name=op.f("ck_workflow_steps_references_match_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'skipped')",
            name=op.f("ck_workflow_steps_status_valid"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name=op.f("ck_workflow_steps_failure_code_valid"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_workflow_steps_lifecycle_fields_match_status"),
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name=op.f("ck_workflow_steps_started_at_not_before_created_at"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= COALESCE(started_at, created_at)",
            name=op.f("ck_workflow_steps_completed_at_not_before_start"),
        ),
    )
    op.create_index(
        "ix_workflow_steps_workflow_status",
        "workflow_steps",
        ["workflow_id", "status"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_workflow_steps_workflow_status", table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index("ix_turn_workflows_status", table_name="turn_workflows")
    op.drop_table("turn_workflows")

    op.execute(
        """
        DELETE FROM command_receipts
        WHERE approval_id IN (
            SELECT ar.id
            FROM approval_requests AS ar
            JOIN tool_invocations AS ti ON ti.id = ar.invocation_id
            WHERE ti.invocation_number <> 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM approval_requests
        WHERE invocation_id IN (
            SELECT id FROM tool_invocations WHERE invocation_number <> 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM policy_evaluations
        WHERE invocation_id IN (
            SELECT id FROM tool_invocations WHERE invocation_number <> 1
        )
        """
    )
    op.execute("DELETE FROM tool_invocations WHERE invocation_number <> 1")

    op.drop_constraint(
        "tool_invocation_workflow_identity",
        "tool_invocations",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_tool_invocations_invocation_number_positive"),
        "tool_invocations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_tool_invocations_invocation_number_day_7"),
        "tool_invocations",
        "invocation_number = 1",
    )
    op.create_unique_constraint(
        "attempt_tool_invocation",
        "tool_invocations",
        ["attempt_id"],
    )
