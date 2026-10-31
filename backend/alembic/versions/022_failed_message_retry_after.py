"""record until when a failed message waits before it is retried

A platform refusing us for a while — YouTube answering caption requests with
429 — is waited out rather than retried every fetch (#125, ADR 0007): what it
refused is not asked for again before the refusal ends.

Additive: one new nullable column. Failures recorded before it, and every
failure that is not a refusal, have none and are retried at once, as before;
the downgrade drops only the column.

Revision ID: 022
Revises: 021
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("failed_messages", sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("failed_messages", "retry_after")
