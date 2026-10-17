import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, ForeignKey, func, Date
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import false as sa_false
from app.core.database import Base


class SourceConfig(Base):
    __tablename__ = "source_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    platform_connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_connections.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fetch_cadence: Mapped[str] = mapped_column(String(20), nullable=False, default="hourly")
    # {text: true, image: false, video: false, url: false}
    content_filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=lambda: {"text": True, "image": False, "video": False, "url": False})
    backfill_start_date: Mapped[datetime | None] = mapped_column(nullable=True)
    # Paused only stops future fetching; the cursor below is left untouched.
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    # When the pause started — what the resume prompt measures the backlog from.
    paused_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # The fetch position: the platform's own handle on it, and the timestamp of
    # the content that handle names — recorded, not inferred, because a position
    # can advance without storing anything (#19). last_fetched_at is a different
    # thing: when we last ran, which drives the cadence.
    last_fetched_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_fetched_item_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=func.now())

    connection: Mapped["PlatformConnection | None"] = relationship("PlatformConnection", back_populates="sources")
    backfill_jobs: Mapped[list["BackfillJob"]] = relationship("BackfillJob", back_populates="source")
    evidence_items: Mapped[list["EvidenceItem"]] = relationship("EvidenceItem", back_populates="source")
