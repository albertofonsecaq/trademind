"""record what an outcome check was priced against

Gold and silver have no spot price on Yahoo, so their futures stand in for it
(#115), and a result decided on a stand-in has to be told apart from one
decided on spot.

Additive: one new nullable column. Checks made before it are left empty —
unknown, rather than a guess at how they were fetched at the time — and the
downgrade drops only the column.

Revision ID: 021
Revises: 020
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outcome_checks", sa.Column("priced_as", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("outcome_checks", "priced_as")
