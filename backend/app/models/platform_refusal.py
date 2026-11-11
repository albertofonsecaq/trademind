from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
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
    # How many refusals in a row earned the current wait, which is how the next
    # one knows to wait longer (#140, ADR 0010). 0 once the platform has
    # answered a request since: the next refusal starts again at the base wait.
    consecutive_refusals: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
