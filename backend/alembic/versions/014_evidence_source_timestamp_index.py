"""index evidence_items by source and timestamp

Every poll tick now asks for the newest stored content per source, so that a
connector whose cursor id cannot be resolved has a date to fall back on (#19).
`evidence_items` only had an index on workspace_id, so that MAX would have been
a sequential scan over the whole table, once per source, every minute.

Index only — no column added, no row rewritten.

Revision ID: 014
Revises: 013
Create Date: 2026-10-17
"""
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_evidence_items_source_timestamp",
        "evidence_items",
        ["source_config_id", "message_timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_items_source_timestamp", table_name="evidence_items")
