"""add needs_reprocessing to evidence_items

Backfills the items that a truncated vision reply left mis-labelled: they were
written as is_on_topic=False with reason "Could not parse vision response", which
is indistinguishable from a genuine off-topic rejection. Those are unknown, not
rejected — set is_on_topic back to NULL and flag them for reprocessing.

Revision ID: 010
Revises: 009
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

_PARSE_FAILURE_REASON = "Could not parse vision response"


def upgrade() -> None:
    op.add_column(
        "evidence_items",
        sa.Column("needs_reprocessing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        f"""
        UPDATE evidence_items
           SET needs_reprocessing = true,
               is_on_topic = NULL
         WHERE relevance_reason = '{_PARSE_FAILURE_REASON}'
        """
    )


def downgrade() -> None:
    # The original is_on_topic=False cannot be restored faithfully; it was wrong.
    op.drop_column("evidence_items", "needs_reprocessing")
