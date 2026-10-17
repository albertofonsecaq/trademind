"""record the fetch position's timestamp on the source

#19 inferred this from MAX(evidence_items.message_timestamp), which is wrong
wherever a position advances without storing evidence: skipping a backlog with
resume-from-now, content that failed a type filter, or content the relevance
check rejected. In those cases the inferred bound rewinds behind the real
position, and a connector recovering from a deleted marker re-fetches — and
re-pays transcription for — content that was deliberately declined.

So the timestamp is recorded where the position is written. Existing rows are
filled once from the evidence they do have, which is the best available answer
for them and no worse than the inference it replaces.

That also retires the per-tick MAX, and with it the index 014 added to make it
affordable; the downgrade puts both back.

Revision ID: 015
Revises: 014
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_configs",
        sa.Column("last_fetched_item_at", sa.DateTime(timezone=True), nullable=True),
    )
    # One-time fill, while 014's index is still there to serve it.
    op.execute(
        """
        UPDATE source_configs sc
           SET last_fetched_item_at = (
               SELECT MAX(ei.message_timestamp)
                 FROM evidence_items ei
                WHERE ei.source_config_id = sc.id
           )
        """
    )
    op.drop_index("ix_evidence_items_source_timestamp", table_name="evidence_items")


def downgrade() -> None:
    op.create_index(
        "ix_evidence_items_source_timestamp",
        "evidence_items",
        ["source_config_id", "message_timestamp"],
    )
    op.drop_column("source_configs", "last_fetched_item_at")
