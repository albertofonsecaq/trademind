import uuid
from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, func, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class BackfillJob(Base):
    __tablename__ = "backfill_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_configs.id"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    date_range_start: Mapped[datetime] = mapped_column(nullable=False)
    date_range_end: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    items_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    source: Mapped["SourceConfig"] = relationship("SourceConfig", back_populates="backfill_jobs")
