"""add label to source_configs

Revision ID: 009
Revises: 008
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_configs", sa.Column("label", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("source_configs", "label")
