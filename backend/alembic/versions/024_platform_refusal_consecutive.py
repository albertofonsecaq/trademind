"""count the refusals in a row behind a platform's cool-down

Every refusal recorded the same hour (#125, ADR 0007), so a block lasting days
re-earned that hour without end: 17 refusals between 2026-11-04 and 2026-11-11,
each one setting 60 minutes again. A refusal met while we were only just let
back in now waits longer than the last one did (#140, ADR 0010), and the only
thing that can say how many have come in a row is the row itself.

Additive: one new column on a table with at most one row per platform. Existing
rows take the default, 1, which reads as "one refusal so far" — the next one
doubles from the base rather than from history nothing recorded. The downgrade
drops only the column, and the cool-down goes back to its flat base.

Revision ID: 024
Revises: 023
Create Date: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_refusals",
        sa.Column("consecutive_refusals", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("platform_refusals", "consecutive_refusals")
