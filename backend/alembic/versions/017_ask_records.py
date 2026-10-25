"""keep the question a user asked and the answer synthesized for it

The Ask view held its transcript in the browser alone, so a reload threw away
every answer the workspace had already paid to synthesize, and the only way
back to one was to ask again and pay again (#72).

Additive: one new table and its index. No existing table or column is touched,
so every row already stored keeps every value it had, and the downgrade drops
only what this created.

Revision ID: 017
Revises: 016
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ask_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # The index for the one query this table serves; app/models/ask_record.py
    # says which query and why.
    op.create_index(
        "ix_ask_records_user_workspace_created",
        "ask_records",
        ["user_id", "workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ask_records_user_workspace_created", table_name="ask_records")
    op.drop_table("ask_records")
