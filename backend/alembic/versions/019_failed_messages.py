"""record each message that failed, so it is retried instead of lost

The fetch position moves past a message whose processing raised, and nothing
recorded which one it was: twelve links and sixty-three videos went that way on
25–26 Oct and could only be recovered by hand (#93, #96, ADR 0005).

Additive: one new table and its indexes. No existing table or column is
touched, so every row already stored keeps every value it had, and the
downgrade drops only what this created.

Revision ID: 019
Revises: 018
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failed_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_config_id", UUID(as_uuid=True), sa.ForeignKey("source_configs.id"), nullable=False),
        sa.Column("stable_id", sa.String(255), nullable=False),
        sa.Column("message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("given_up_at", sa.DateTime(timezone=True), nullable=True),
    )
    # app/models/failed_message.py says what each is for.
    op.create_unique_constraint(
        "uq_failed_messages_source_stable_id", "failed_messages", ["source_config_id", "stable_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_failed_messages_source_stable_id", "failed_messages", type_="unique")
    op.drop_table("failed_messages")
