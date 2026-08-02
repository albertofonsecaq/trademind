import uuid
from datetime import datetime, timezone
from sqlalchemy import String, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # auth_provider_id is reserved for future OAuth provider IDs
    auth_provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(2), default="en", nullable=False)
    platform_role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), server_default=func.now())

    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship("WorkspaceMember", back_populates="user")
