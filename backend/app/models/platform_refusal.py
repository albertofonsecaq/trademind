from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import now_utc
from app.core.database import Base


class PlatformRefusal(Base):
    """
    Until when a platform is not asked again, after it refused us (#130, ADR 0009).

    One row per platform. It lives in the database so every fetch — the poller,
    a manual fetch, a backfill, a retry — and a restarted process see the same
    refusal, rather than each asking once more and being refused again.
    """
    __tablename__ = "platform_refusals"

    platform: Mapped[str] = mapped_column(String(50), primary_key=True)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # What the platform said, for whoever looks: "HTTP 429", "IpBlocked".
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    refused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())
