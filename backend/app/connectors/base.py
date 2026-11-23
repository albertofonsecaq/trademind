"""
Abstract connector interface. Every ingestion source implements this.
Adding a new source = new connector class, no changes to the pipeline.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator
import uuid

from app.core.clock import as_utc
from app.core.content_unavailable import ContentUnavailable


# Reading a value *in* as aware UTC is the same operation wherever it happens,
# so the connectors use the clock's rather than keeping a second copy of it.
# Imported here because this is the layer whose contract it expresses.
to_aware_utc = as_utc

# There is deliberately no to_naive_utc any more. ADR 1 keeps the connector
# layer as the one place a naive value *could* be legitimate, and #4 added that
# helper for Telethon and YouTube — but neither insists on naive, so it spent
# #34 through #36 with no caller at all and was deleted in #37. If a future
# platform does demand one, it belongs here, converting on the way out to that
# API and nowhere else. The shape is in this comment and in git history.


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
    timestamp: datetime     # aware UTC — see ADR 1; connectors normalize on the way in
    source_type: str = "telegram"           # "telegram" | "youtube"
    content_type: str = "text"              # "text" | "video_transcript"
    language: str | None = None
    metadata: dict | None = None
    # Pre-pipeline cost already incurred (e.g. Whisper transcription) — logged by process_message
    pre_pipeline_cost_usd: str = "0"        # Decimal as string to avoid dataclass/Decimal issues
    pre_pipeline_task_type: str | None = None
    pre_pipeline_model: str | None = None
    # Why the connector could not fetch this message's content — a video YouTube
    # refused captions for (#125), or one with no transcript (#126). The
    # pipeline records the message as failed with it, without processing it.
    fetch_failure: ContentUnavailable | None = None


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
    async def fetch_one(self, stable_id: str) -> RawMessage | None:
        """The message with this stable_id, fetched from the platform again.

        How a failed message is retried (ADR 0005): the message as it is now,
        not a copy kept from the attempt that failed. None when the platform no
        longer has it, which gives the failure up — so None must mean gone, not
        "could not ask": a failure to ask raises, and counts as an attempt.
        """
        ...

    async def fetch_again(self, stable_id: str) -> list[RawMessage] | None:
        """
        Everything a failed message stands for, fetched again: itself, as
        fetch_one has it, unless the platform hands it over in parts — a video
        refused captions comes back as its transcript's chunks (#125). None,
        as for fetch_one, when the platform no longer has it.
        """
        msg = await self.fetch_one(stable_id)
        return None if msg is None else [msg]

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

        Bounds may be naive or aware depending on where the caller read them;
        implementations normalize with to_aware_utc (ADR 1).
        """
        ...
