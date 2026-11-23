"""keep the picture image evidence was read from

Vision turned each image into text and the picture was dropped, so a chart a
trader posted could be described but never shown (#108).

Additive: one new table. No existing table or column is touched, so every row
already stored keeps every value it had, and the downgrade drops only what this
created.

Revision ID: 020
Revises: 019
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # app/models/evidence_image.py says what it is for.
    op.create_table(
        "evidence_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("evidence_item_id", UUID(as_uuid=True), sa.ForeignKey("evidence_items.id"), nullable=False, unique=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("evidence_images")
