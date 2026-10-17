"""
Abstract connector interface. Every ingestion source implements this.
Adding a new source = new connector class, no changes to the pipeline.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator
import uuid


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to the connector contract: naive, UTC.

    Connectors speak naive UTC, but the timestamp columns these bounds come from
    are timestamptz, so a value read back from the DB arrives tz-aware. Comparing
    the two raises TypeError, so normalize at the boundary rather than asking
    every caller to remember (#4).
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class FetchPosition:
    """
    How far through a source we have already got — see CONTEXT.md.

    Two fields because platforms hand it to us in two shapes. `stable_id` is the
    platform's own handle on the position; `item_at` is the timestamp of the
    content that handle names. A platform whose handle is a bound (Telegram's
    message id) needs only the first. A platform whose handle is a marker it has
    to find again (YouTube's video id) loses it when that item is deleted, and
    falls back to the timestamp — which nothing can delete (#19).

    `item_at` is recorded wherever the position is written, including when a
    position is advanced without ingesting anything: skipping a backlog, or
    content that failed a filter. Inferring it afterwards from stored evidence
    would get those cases wrong.
    """
    stable_id: str | None = None
    item_at: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return self.stable_id is None and self.item_at is None


@dataclass
class RawMessage:
    """Normalized output of any connector's fetch step."""
    stable_id: str          # globally unique: e.g. "telegram:{channel_id}:{msg_id}"
    source_config_id: uuid.UUID
    workspace_id: uuid.UUID
    text: str | None
    author: str | None
    channel: str | None
    timestamp: datetime
    source_type: str = "telegram"           # "telegram" | "youtube"
    content_type: str = "text"              # "text" | "video_transcript"
    language: str | None = None
    metadata: dict | None = None
    # Pre-pipeline cost already incurred (e.g. Whisper transcription) — logged by process_message
    pre_pipeline_cost_usd: str = "0"        # Decimal as string to avoid dataclass/Decimal issues
    pre_pipeline_task_type: str | None = None
    pre_pipeline_model: str | None = None


class BaseConnector(ABC):
    @abstractmethod
    async def fetch_new(self, position: FetchPosition) -> AsyncIterator[RawMessage]:
        """Yield new content after `position`.

        No default on purpose: a connector whose position is a marker it must
        find again stops yielding for good when that item is deleted, so a new
        implementer has to decide what it does with the fallback rather than
        inherit a silent None (#19).
        """
        ...

    def repaired_position(self) -> FetchPosition | None:
        """
        A position to store instead, after a fetch whose own position was
        unusable — read once, after iterating `fetch_new`.

        Without it a connector that recovered from a deleted marker would be
        handed the same dead marker on the next tick, re-running the recovery
        and re-logging it every minute for ever. The default is None: a
        connector whose position is a bound can never be in this state.
        """
        return None

    @abstractmethod
    async def latest_stable_id(self) -> str | None:
        """The newest available evidence's stable_id, without ingesting anything.

        Used to skip a backlog: the fetch cursor is moved to this id, so the
        next poll starts after it. Returns None when the source has nothing to
        report, which callers must treat as "unknown" rather than "start over" —
        clearing a cursor would re-ingest the source's whole history.
        """
        ...

    @abstractmethod
    async def fetch_range(
        self, date_start: datetime, date_end: datetime | None
    ) -> AsyncIterator[RawMessage]:
        """Yield messages in [date_start, date_end] for backfill.

        Bounds may be naive or aware; implementations normalize with to_naive_utc.
        """
        ...
