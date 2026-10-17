"""count a backfill's failures on the job

A job recorded only its successes, so "completed, 1043 ingested" was
indistinguishable from "1043 ingested and 1610 dropped to a provider limit" —
the only trace of the loss was in a log that rotates (#9).

Additive: one counter, defaulting to zero, so existing rows keep every value
they had. They stay "completed" with items_failed = 0, which is what was
recorded about them; nothing is retro-fitted.

Revision ID: 016
Revises: 015
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backfill_jobs",
        sa.Column("items_failed", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("backfill_jobs", "items_failed")
