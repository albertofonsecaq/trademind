import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import now_utc
from app.core.database import Base


class EvidenceImage(Base):
    """
    The picture a piece of image evidence was read from (#108).

    Vision turns an image into the evidence's text, and until #108 the picture
    was dropped after that, so a chart could be described but never shown. It
    is kept in the database rather than on disk so the verified dump that backs
    up the evidence backs up its picture too. A table of its own keeps the
    bytes out of every query that reads evidence rows.
    """
    __tablename__ = "evidence_images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # One picture per piece of evidence.
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False, unique=True
    )
    # The evidence's own workspace, so serving the picture can check it directly.
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())
