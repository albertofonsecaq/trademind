import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, ForeignKey, func, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class TradeIdea(Base):
    __tablename__ = "trade_ideas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    source_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    setup_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True)  # long|short|info|commentary
    entry: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    target: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    summary_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_es: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_timestamp: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=func.now())

    evidence_item: Mapped["EvidenceItem"] = relationship("EvidenceItem", back_populates="trade_ideas")
