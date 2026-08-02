"""phase2 schema

Revision ID: 002
Revises: 001
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.create_table(
        "platform_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("session_credential", sa.Text, nullable=True),
        sa.Column("connected_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("auth_metadata", JSONB, nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_platform_connections_workspace_id", "platform_connections", ["workspace_id"])

    op.create_table(
        "source_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("platform_connection_id", UUID(as_uuid=True), sa.ForeignKey("platform_connections.id"), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("fetch_cadence", sa.String(20), nullable=False, server_default="hourly"),
        sa.Column("content_filters", JSONB, nullable=False, server_default='{"text":true,"image":false,"video":false,"url":false}'),
        sa.Column("backfill_start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fetched_id", sa.String(255), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_source_configs_workspace_id", "source_configs", ["workspace_id"])

    op.create_table(
        "backfill_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_config_id", UUID(as_uuid=True), sa.ForeignKey("source_configs.id"), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("date_range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_range_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("items_ingested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("triggered_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "evidence_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_config_id", UUID(as_uuid=True), sa.ForeignKey("source_configs.id"), nullable=False),
        sa.Column("stable_id", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("original_language", sa.String(10), nullable=True),
        sa.Column("is_on_topic", sa.Boolean, nullable=True),
        sa.Column("relevance_reason", sa.String(500), nullable=True),
        sa.Column("source_metadata", JSONB, nullable=True),
        sa.Column("message_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "stable_id", name="uq_evidence_stable_id"),
    )
    op.create_index("ix_evidence_items_workspace_id", "evidence_items", ["workspace_id"])

    op.create_table(
        "trade_ideas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("evidence_item_id", UUID(as_uuid=True), sa.ForeignKey("evidence_items.id"), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_language", sa.String(10), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("setup_type", sa.String(100), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("entry", sa.Numeric(20, 8), nullable=True),
        sa.Column("target", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop", sa.Numeric(20, 8), nullable=True),
        sa.Column("summary_en", sa.Text, nullable=True),
        sa.Column("summary_es", sa.Text, nullable=True),
        sa.Column("original_text", sa.Text, nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("channel", sa.String(255), nullable=True),
        sa.Column("message_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_trade_ideas_workspace_id", "trade_ideas", ["workspace_id"])
    op.create_index("ix_trade_ideas_symbol", "trade_ideas", ["symbol"])

    op.create_table(
        "embedding_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("source_language", sa.String(10), nullable=True),
        sa.Column("embedding_metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "source_id", name="uq_embedding_source"),
    )
    # Add vector column separately (pgvector extension required)
    op.execute(f"ALTER TABLE embedding_rows ADD COLUMN embedding vector({EMBEDDING_DIM}) NOT NULL DEFAULT array_fill(0, ARRAY[{EMBEDDING_DIM}])::vector")
    op.execute("ALTER TABLE embedding_rows ALTER COLUMN embedding DROP DEFAULT")
    # IVFFlat index for ANN search (build after data is loaded)
    op.execute("CREATE INDEX IF NOT EXISTS ix_embedding_rows_workspace_id ON embedding_rows (workspace_id)")
    # Full-text search via generated tsvector index
    op.execute("CREATE INDEX IF NOT EXISTS ix_embedding_rows_fts ON embedding_rows USING GIN (to_tsvector('english', text))")


def downgrade() -> None:
    op.drop_table("embedding_rows")
    op.drop_table("trade_ideas")
    op.drop_table("evidence_items")
    op.drop_table("backfill_jobs")
    op.drop_table("source_configs")
    op.drop_table("platform_connections")
