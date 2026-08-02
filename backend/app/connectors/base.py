"""
Abstract connector interface. Every ingestion source implements this.
Adding a new source = new connector class, no changes to the pipeline.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator
import uuid


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
    async def fetch_new(self, since_id: str | None) -> AsyncIterator[RawMessage]:
        """Yield new messages since the last fetched stable_id."""
        ...

    @abstractmethod
    async def fetch_range(
        self, date_start: datetime, date_end: datetime | None
    ) -> AsyncIterator[RawMessage]:
        """Yield messages in [date_start, date_end] for backfill."""
        ...
