"""add policy evaluations and approvals

Revision ID: b8d9e0f1a2b3
Revises: a7c8d9e0f1a2
Create Date: 2026-07-15 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d9e0f1a2b3"
down_revision: str | None = "a7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        "tool_invocation_policy_identity",
        "tool_invocations",
        [
            "id",
            "turn_id",
            "attempt_id",
            "tool_definition_id",
            "tool_version_id",
        ],
    )
    op.create_table(
        "policy_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("requester_actor_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=True),
        sa.Column("tool_definition_id", sa.Uuid(), nullable=False),
        sa.Column("tool_version_id", sa.Uuid(), nullable=False),
        sa.Column("effect", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("required_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("granted_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_version", sa.String(length=50), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("fingerprint_version", sa.String(length=32), nullable=False),
        sa.Column("fingerprint_digest", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('allow', 'deny', 'require_confirmation', 'require_elevated_approval')",
            name=op.f("ck_policy_evaluations_decision_valid"),
        ),
        sa.CheckConstraint(
            "(decision = 'allow' AND effect = 'read_only') "
            "OR (decision = 'require_confirmation' AND effect = 'mutating') "
            "OR decision IN ('deny', 'require_elevated_approval')",
            name=op.f("ck_policy_evaluations_decision_matches_effect"),
        ),
        sa.CheckConstraint(
            "environment IN ('development', 'test', 'production')",
            name=op.f("ck_policy_evaluations_environment_valid"),
        ),
        sa.CheckConstraint(
            "effect IN ('read_only', 'mutating', 'external_side_effect', 'privileged')",
            name=op.f("ck_policy_evaluations_effect_valid"),
        ),
        sa.CheckConstraint(
            "fingerprint_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_policy_evaluations_fingerprint_digest_valid"),
        ),
        sa.CheckConstraint(
            "fingerprint_version = 'action-v1'",
            name=op.f("ck_policy_evaluations_fingerprint_version_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(granted_scopes) = 'array' "
            "AND jsonb_array_length(granted_scopes) BETWEEN 0 AND 32",
            name=op.f("ck_policy_evaluations_granted_scopes_bounded_array"),
        ),
        sa.CheckConstraint(
            "policy_version = 'day8-v1'",
            name=op.f("ck_policy_evaluations_policy_version_valid"),
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9._-]{0,99}$'",
            name=op.f("ck_policy_evaluations_reason_code_valid"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_scopes) = 'array' "
            "AND jsonb_array_length(required_scopes) BETWEEN 1 AND 32",
            name=op.f("ck_policy_evaluations_required_scopes_bounded_array"),
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "attempt_id"],
            ["turn_attempts.turn_id", "turn_attempts.id"],
            name=op.f("fk_policy_evaluations_turn_id_turn_attempts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            name=op.f("fk_policy_evaluations_agent_version_id_agent_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "tool_definition_id"],
            ["tool_definitions.team_id", "tool_definitions.id"],
            name=op.f("fk_policy_evaluations_team_id_tool_definitions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id", "tool_version_id"],
            ["tool_versions.tool_definition_id", "tool_versions.id"],
            name=op.f("fk_policy_evaluations_tool_definition_id_tool_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "invocation_id",
                "turn_id",
                "attempt_id",
                "tool_definition_id",
                "tool_version_id",
            ],
            [
                "tool_invocations.id",
                "tool_invocations.turn_id",
                "tool_invocations.attempt_id",
                "tool_invocations.tool_definition_id",
                "tool_invocations.tool_version_id",
            ],
            name=op.f("fk_policy_evaluations_invocation_id_tool_invocations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_evaluations")),
        sa.UniqueConstraint(
            "id",
            "team_id",
            "requester_actor_id",
            "invocation_id",
            "fingerprint_version",
            "fingerprint_digest",
            "tool_definition_id",
            "tool_version_id",
            "effect",
            name="policy_evaluation_approval_identity",
        ),
    )
    op.create_index(
        "ix_policy_evaluations_invocation",
        "policy_evaluations",
        ["invocation_id", "evaluated_at"],
        unique=False,
    )
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("policy_evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("requester_actor_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint_version", sa.String(length=32), nullable=False),
        sa.Column("fingerprint_digest", sa.String(length=64), nullable=False),
        sa.Column("tool_definition_id", sa.Uuid(), nullable=False),
        sa.Column("tool_version_id", sa.Uuid(), nullable=False),
        sa.Column("effect", sa.String(length=32), nullable=False),
        sa.Column("argument_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_approval_requests_expires_at_after_created_at"),
        ),
        sa.CheckConstraint(
            "effect = 'mutating'",
            name=op.f("ck_approval_requests_effect_requires_confirmation"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(argument_fields) = 'array'",
            name=op.f("ck_approval_requests_argument_fields_is_array"),
        ),
        sa.CheckConstraint(
            "fingerprint_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_approval_requests_fingerprint_digest_valid"),
        ),
        sa.CheckConstraint(
            "fingerprint_version = 'action-v1'",
            name=op.f("ck_approval_requests_fingerprint_version_valid"),
        ),
        sa.CheckConstraint(
            """
            (
                status = 'pending'
                AND resolved_by_actor_id IS NULL
                AND resolved_at IS NULL
                AND consumed_at IS NULL
            )
            OR (
                status IN ('approved', 'rejected')
                AND resolved_by_actor_id IS NOT NULL
                AND resolved_at IS NOT NULL
                AND resolved_at < expires_at
                AND consumed_at IS NULL
            )
            OR (
                status = 'expired'
                AND resolved_by_actor_id IS NULL
                AND resolved_at IS NOT NULL
                AND resolved_at >= expires_at
                AND consumed_at IS NULL
            )
            OR (
                status = 'consumed'
                AND resolved_by_actor_id IS NOT NULL
                AND resolved_at IS NOT NULL
                AND consumed_at IS NOT NULL
                AND resolved_at < expires_at
                AND consumed_at >= resolved_at
                AND consumed_at < expires_at
            )
            """,
            name=op.f("ck_approval_requests_lifecycle_fields_match_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')",
            name=op.f("ck_approval_requests_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            [
                "policy_evaluation_id",
                "team_id",
                "requester_actor_id",
                "invocation_id",
                "fingerprint_version",
                "fingerprint_digest",
                "tool_definition_id",
                "tool_version_id",
                "effect",
            ],
            [
                "policy_evaluations.id",
                "policy_evaluations.team_id",
                "policy_evaluations.requester_actor_id",
                "policy_evaluations.invocation_id",
                "policy_evaluations.fingerprint_version",
                "policy_evaluations.fingerprint_digest",
                "policy_evaluations.tool_definition_id",
                "policy_evaluations.tool_version_id",
                "policy_evaluations.effect",
            ],
            name=op.f("fk_approval_requests_policy_evaluation_id_policy_evaluations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    op.create_index(
        "ix_approval_requests_team_status_expiry",
        "approval_requests",
        ["team_id", "status", "expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_approval_requests_active_invocation",
        "approval_requests",
        ["invocation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'approved')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_approval_requests_active_invocation",
        table_name="approval_requests",
        postgresql_where=sa.text("status IN ('pending', 'approved')"),
    )
    op.drop_index(
        "ix_approval_requests_team_status_expiry",
        table_name="approval_requests",
    )
    op.drop_table("approval_requests")
    op.drop_index(
        "ix_policy_evaluations_invocation",
        table_name="policy_evaluations",
    )
    op.drop_table("policy_evaluations")
    op.drop_constraint(
        "tool_invocation_policy_identity",
        "tool_invocations",
        type_="unique",
    )
