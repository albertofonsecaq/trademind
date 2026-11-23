"""record each pattern-mining and validation run, and how it ended

Both ran as fire-and-forget background tasks: the request that started one
answered "started" with a count hard-coded to 0, and how the run went existed
only in the server log. A run whose every call failed on a rejected API key
looked exactly like one that found nothing (#86).

Additive: one new table and its indexes. No existing table or column is
touched, so every row already stored keeps every value it had, and the
downgrade drops only what this created.

Revision ID: 018
Revises: 017
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("triggered_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("cards_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cards_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", JSONB, nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # app/models/analysis_run.py says what each index is for.
    op.create_index(
        "uq_analysis_runs_one_running",
        "analysis_runs",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_analysis_runs_workspace_kind_started",
        "analysis_runs",
        ["workspace_id", "kind", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_workspace_kind_started", table_name="analysis_runs")
    op.drop_index("uq_analysis_runs_one_running", table_name="analysis_runs")
    op.drop_table("analysis_runs")
