"""add paused state to source_configs

Additive only: one new column with a server-side default, so existing rows keep
every value they had — including their fetch cursor — and start out unpaused.
The downgrade drops the column and nothing else.

Revision ID: 011
Revises: 010
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_configs",
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("source_configs", "is_paused")
