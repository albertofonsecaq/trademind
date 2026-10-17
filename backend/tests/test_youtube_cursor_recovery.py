"""
Tests for #19 — a YouTube source must survive losing the video its cursor names.

The YouTube cursor is a marker, not a bound: fetch_new walks the channel window
looking for the video id inside last_fetched_id and yields what comes after it.
If that video is deleted, privatized, or simply falls out of the window, the
marker is never found and every video gets skipped — the source stops ingesting
for good, silently. Telegram cannot fail this way, because min_id is a bound.

The fix is a bound to fall back on: the published date of the newest content
already stored for that source. A date cannot be deleted, so recovery is
automatic, and because the bound is the newest thing we already hold, nothing is
ingested or paid for twice.
"""
import logging
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.connectors.base import RawMessage
from app.connectors.youtube import YouTubeConnector


def _video(video_id: str, published: str) -> dict:
    return {"video_id": video_id, "title": video_id, "published_at": published}


# the channel window, newest first, as the API returns it
WINDOW = [
    _video("v3", "2026-10-16T09:00:00Z"),
    _video("v2", "2026-10-15T09:00:00Z"),
    _video("v1", "2026-10-10T09:00:00Z"),
]


@pytest.fixture
def connector(monkeypatch):
    c = YouTubeConnector(
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        channel_identifier="@chan",
    )

    async def _videos():
        return WINDOW

    async def _chunks(video):
        yield RawMessage(
            stable_id=f"youtube:{video['video_id']}:full",
            source_config_id=c.source_config_id,
            workspace_id=c.workspace_id,
            text="transcript",
            author=None,
            channel="@chan",
            timestamp=c._parse_timestamp(video["published_at"]),
            source_type="youtube",
            content_type="video_transcript",
        )

    monkeypatch.setattr(c, "_get_videos", _videos)
    monkeypatch.setattr(c, "_yield_video_chunks", _chunks)
    return c


async def _ids(connector, **kwargs) -> list[str]:
    return [msg.stable_id async for msg in connector.fetch_new(**kwargs)]


class TestMarkerStillPresent:
    async def test_yields_only_what_came_after_it(self, connector):
        """The happy path has to keep working untouched."""
        got = await _ids(connector, since_id="youtube:v2:full")

        assert got == ["youtube:v3:full"]

    async def test_a_date_bound_does_not_override_a_found_marker(self, connector):
        got = await _ids(
            connector,
            since_id="youtube:v1:full",
            since_timestamp=datetime(2026, 10, 15, 12, 0),
        )

        assert got == ["youtube:v2:full", "youtube:v3:full"]


class TestMarkerGone:
    async def test_falls_back_to_the_published_date(self, connector):
        """The bug: this used to yield nothing, for ever."""
        got = await _ids(
            connector,
            since_id="youtube:deleted-video:full",
            since_timestamp=datetime(2026, 10, 14, 0, 0),
        )

        assert got == ["youtube:v2:full", "youtube:v3:full"]

    async def test_does_not_re_ingest_what_is_already_stored(self, connector):
        """
        The bound is the newest content already stored, so the recovery pays for
        new videos only — no re-transcription of what the workspace holds.
        """
        got = await _ids(
            connector,
            since_id="youtube:deleted-video:full",
            since_timestamp=datetime(2026, 10, 15, 9, 0),      # v2's own date
        )

        assert got == ["youtube:v3:full"]

    async def test_says_so_in_the_logs(self, connector, caplog):
        with caplog.at_level(logging.WARNING):
            await _ids(
                connector,
                since_id="youtube:deleted-video:full",
                since_timestamp=datetime(2026, 10, 14, 0, 0),
            )

        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deleted-video" in w for w in warnings), warnings

    async def test_the_whole_window_is_the_last_resort(self, connector):
        """
        No marker and no bound — a source whose evidence was never stored, or
        was cleared. Ingesting the window is safe: the pipeline's stable_id
        dedup absorbs anything already held.
        """
        got = await _ids(connector, since_id="youtube:deleted-video:full")

        assert got == ["youtube:v1:full", "youtube:v2:full", "youtube:v3:full"]


class TestFirstEverFetch:
    async def test_takes_the_whole_window(self, connector):
        got = await _ids(connector, since_id=None)

        assert got == ["youtube:v1:full", "youtube:v2:full", "youtube:v3:full"]


class TestThePipelineSuppliesTheBound:
    async def test_passes_the_newest_stored_timestamp_to_the_connector(self, monkeypatch):
        """
        The connector cannot read the database, so the bound has to arrive from
        the pipeline — the newest message_timestamp already stored for the
        source. Without this the fallback never engages in production.
        """
        from app.services import ingestion_pipeline

        seen: dict = {}

        class _Connector:
            async def fetch_new(self, since_id=None, since_timestamp=None):
                seen["since_id"] = since_id
                seen["since_timestamp"] = since_timestamp
                return
                yield       # pragma: no cover — an empty async generator

        newest = datetime(2026, 10, 15, 9, 0)

        class _Session:
            async def execute(self, _stmt):
                return SimpleNamespace(
                    scalar_one_or_none=lambda: newest,
                    scalar=lambda: newest,
                )

            async def commit(self):
                pass

            def begin_nested(self):
                raise AssertionError("no message should be processed here")

        monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda source: _Connector())

        source = SimpleNamespace(
            id=uuid.uuid4(), content_filters={"text": True},
            last_fetched_id="youtube:v2:full", source_type="youtube",
        )
        workspace = SimpleNamespace(topic_scope="trading")

        await ingestion_pipeline.run_fetch_pipeline(
            _Session(), source=source, workspace=workspace
        )

        assert seen["since_id"] == "youtube:v2:full"
        assert seen["since_timestamp"] == newest
