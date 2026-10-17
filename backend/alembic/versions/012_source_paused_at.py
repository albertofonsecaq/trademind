"""add paused_at to source_configs

The resume prompt has to say how much backlog is waiting, and "paused since X"
plus the existing last_fetched_at is the honest answer that costs no API calls
(#12). Additive: one nullable column, existing rows unaffected.

Revision ID: 012
Revises: 011
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_configs",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_configs", "paused_at")
