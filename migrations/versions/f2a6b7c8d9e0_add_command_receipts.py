"""add command receipts

Revision ID: f2a6b7c8d9e0
Revises: 928edc5afeb0
Create Date: 2026-07-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a6b7c8d9e0"
down_revision: str | Sequence[str] | None = "928edc5afeb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "command_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("command_scope", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_command_receipts_idempotency_key_hash_valid"),
        ),
        sa.CheckConstraint(
            "operation IN ('create_conversation', 'continue_conversation')",
            name=op.f("ck_command_receipts_operation_valid"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_command_receipts_request_fingerprint_valid"),
        ),
        sa.CheckConstraint(
            """
            (operation = 'create_conversation' AND command_scope = 'create')
            OR (
                operation = 'continue_conversation'
                AND command_scope = conversation_id::text
            )
            """,
            name=op.f("ck_command_receipts_scope_matches_operation"),
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["turn_attempts.id"],
            name=op.f("fk_command_receipts_attempt_id_turn_attempts"),
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_command_receipts_conversation_id_conversations"),
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_command_receipts_message_id_messages"),
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["turns.id"],
            name=op.f("fk_command_receipts_turn_id_turns"),
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command_receipts")),
        sa.UniqueConstraint(
            "team_id",
            "operation",
            "command_scope",
            "idempotency_key_hash",
            name="command_receipt_authority",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("command_receipts")
