"""add ingestion_paused to workspaces

One switch for a whole workspace, orthogonal to each source's own paused state
(#13). Additive: one column with a server-side default, so existing workspaces
keep every value they had and start out ingesting.

Revision ID: 013
Revises: 012
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("ingestion_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "ingestion_paused")
