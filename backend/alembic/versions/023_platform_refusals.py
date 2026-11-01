"""record until when a platform that refused us is left alone

The cool-down after YouTube refused caption requests lived in the running
process (#125): a restart forgot it and asked again, and fetches running side
by side could each ask before it was set (#130). It is kept here instead, one
row per platform, where every fetch reads it before asking.

Additive: one new table, empty until a platform refuses; the downgrade drops
only that table. Its privileges for the application come from the default
privileges setup_db_roles.py grants (ADR 0004).

Revision ID: 023
Revises: 022
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_refusals",
        sa.Column("platform", sa.String(50), primary_key=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("refused_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("platform_refusals")
