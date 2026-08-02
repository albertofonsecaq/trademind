import uuid
from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, func, Text, Float, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (UniqueConstraint("workspace_id", "stable_id", name="uq_evidence_stable_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    source_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_configs.id"), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(255), nullable=False)  # "telegram:{chan}:{msg_id}"
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # text|image|video_transcript|url
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    original_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_on_topic: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # null = not yet checked
    relevance_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    message_timestamp: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=func.now())

    source: Mapped["SourceConfig"] = relationship("SourceConfig", back_populates="evidence_items")
    trade_ideas: Mapped[list["TradeIdea"]] = relationship("TradeIdea", back_populates="evidence_item")
