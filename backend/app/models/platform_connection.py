import uuid
from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, func, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class PlatformConnection(Base):
    __tablename__ = "platform_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # "telegram"
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    session_credential: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    connected_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # Stores temporary auth state (phone_code_hash, phone) between OTP steps
    auth_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)

    sources: Mapped[list["SourceConfig"]] = relationship("SourceConfig", back_populates="connection")
