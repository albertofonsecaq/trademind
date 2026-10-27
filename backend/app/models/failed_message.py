import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import now_utc
from app.core.database import Base

# How many failed attempts a message gets before it is given up (ADR 0005).
MAX_ATTEMPTS = 3


class FailedMessage(Base):
    """
    A message whose processing raised during a fetch or a backfill (#96, ADR 0005).

    The fetch position moves past it regardless, so this row is the only thing
    that remembers it: each fetch of the source retries it first, a success
    deletes the row, and after MAX_ATTEMPTS it is given up and kept for a person.
    """
    __tablename__ = "failed_messages"
    __table_args__ = (
        # One row per message: a second failure counts another attempt on it.
        UniqueConstraint("source_config_id", "stable_id", name="uq_failed_messages_source_stable_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    source_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_configs.id"), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # When the message was posted, so a person can find it on the platform.
    message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The last attempt's reason, already safe to show (no keys, no stack trace).
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())
    # Set when retrying stops; the row stays, for a person.
    given_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
