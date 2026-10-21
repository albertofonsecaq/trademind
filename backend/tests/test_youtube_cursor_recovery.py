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

from app.connectors.base import FetchPosition, RawMessage
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


async def _ids(connector, stable_id=None, item_at=None) -> list[str]:
    position = FetchPosition(stable_id=stable_id, item_at=item_at)
    return [msg.stable_id async for msg in connector.fetch_new(position)]


class TestMarkerStillPresent:
    async def test_yields_only_what_came_after_it(self, connector):
        """The happy path has to keep working untouched."""
        got = await _ids(connector, stable_id="youtube:v2:full")

        assert got == ["youtube:v3:full"]

    async def test_a_date_bound_does_not_override_a_found_marker(self, connector):
        got = await _ids(
            connector,
            stable_id="youtube:v1:full",
            item_at=datetime(2026, 10, 15, 12, 0),
        )

        assert got == ["youtube:v2:full", "youtube:v3:full"]


class TestMarkerGone:
    async def test_falls_back_to_the_published_date(self, connector):
        """The bug: this used to yield nothing, for ever."""
        got = await _ids(
            connector,
            stable_id="youtube:deleted-video:full",
            item_at=datetime(2026, 10, 14, 0, 0),
        )

        assert got == ["youtube:v2:full", "youtube:v3:full"]

    async def test_does_not_re_ingest_the_window_it_has_already_passed(self, connector):
        """
        The bound is where the position already was, so recovery pays for newer
        videos only — no re-transcription of the window behind it.
        """
        got = await _ids(
            connector,
            stable_id="youtube:deleted-video:full",
            item_at=datetime(2026, 10, 15, 12, 0),             # after v2, before v3
        )

        assert got == ["youtube:v3:full"]

    async def test_keeps_a_video_published_in_the_bound_second(self, connector):
        """
        YouTube publishes at second resolution, so batch uploads and premieres
        share a timestamp. Excluding the bound itself would skip those siblings
        on every future tick, silently and for ever; re-offering that one second
        costs at most a transcript the dedup then discards.
        """
        got = await _ids(
            connector,
            stable_id="youtube:deleted-video:full",
            item_at=datetime(2026, 10, 15, 9, 0),              # v2's own second
        )

        assert "youtube:v2:full" in got

    async def test_says_so_in_the_logs(self, connector, caplog):
        with caplog.at_level(logging.WARNING):
            await _ids(
                connector,
                stable_id="youtube:deleted-video:full",
                item_at=datetime(2026, 10, 14, 0, 0),
            )

        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deleted-video" in w for w in warnings), warnings

    async def test_the_whole_window_is_the_last_resort(self, connector):
        """
        No marker and no bound — a source whose evidence was never stored, or
        was cleared. Ingesting the window is safe: the pipeline's stable_id
        dedup absorbs anything already held.
        """
        got = await _ids(connector, stable_id="youtube:deleted-video:full")

        assert got == ["youtube:v1:full", "youtube:v2:full", "youtube:v3:full"]


class TestFirstEverFetch:
    async def test_takes_the_whole_window(self, connector):
        got = await _ids(connector)

        assert got == ["youtube:v1:full", "youtube:v2:full", "youtube:v3:full"]


class _PipelineSession:
    """Records statements so a test can pin what the pipeline asks the DB for."""

    def __init__(self, source=None):
        self.statements: list[str] = []
        self._source = source

    async def execute(self, stmt):
        self.statements.append(str(stmt).lower())
        return SimpleNamespace(scalar_one_or_none=lambda: self._source, scalar=lambda: None)

    async def commit(self):
        pass

    def begin_nested(self):
        raise AssertionError("no message should be processed here")


class TestThePipelineSuppliesThePosition:
    async def test_hands_the_connector_the_recorded_position(self, monkeypatch):
        """
        The connector cannot read the database, so the position arrives from the
        pipeline — and from the source's own columns, not inferred from stored
        evidence, which lags wherever a position advanced without storing
        anything.
        """
        from app.services import ingestion_pipeline

        seen: dict = {}

        class _Connector:
            async def fetch_new(self, position):
                seen["position"] = position
                return
                yield       # pragma: no cover — an empty async generator

            def repaired_position(self):
                return None

        monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda source: _Connector())

        recorded = datetime(2026, 10, 15, 9, 0)
        source = SimpleNamespace(
            id=uuid.uuid4(), content_filters={"text": True}, source_type="youtube",
            last_fetched_id="youtube:v2:full", last_fetched_item_at=recorded,
            platform_connection_id=None,        # YouTube has no connection
        )
        db = _PipelineSession()

        await ingestion_pipeline.run_fetch_pipeline(
            db, source=source, workspace=SimpleNamespace(topic_scope="trading")
        )

        assert seen["position"] == FetchPosition(
            stable_id="youtube:v2:full", item_at=recorded
        )
        # The retired inference: nothing should be asking evidence for a bound.
        assert not any("evidence_items" in stmt for stmt in db.statements)

    async def test_stores_a_repaired_position_so_recovery_runs_once(self, monkeypatch):
        """
        A connector whose own position was dead reports where it got to. Without
        persisting that, the next tick is handed the same dead marker and the
        recovery — and its warning — repeat every minute for ever.
        """
        from app.services import ingestion_pipeline

        repaired = FetchPosition(
            stable_id="youtube:v3:full", item_at=datetime(2026, 10, 16, 9, 0)
        )

        class _Connector:
            async def fetch_new(self, position):
                return
                yield       # pragma: no cover — yielded nothing, as in the real case

            def repaired_position(self):
                return repaired

        monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda source: _Connector())

        source = SimpleNamespace(
            id=uuid.uuid4(), content_filters={"text": True}, source_type="youtube",
            last_fetched_id="youtube:deleted:full", last_fetched_item_at=None,
            last_fetched_at=None, platform_connection_id=None,
        )

        await ingestion_pipeline.run_fetch_pipeline(
            _PipelineSession(source), source=source,
            workspace=SimpleNamespace(topic_scope="trading"),
        )

        assert source.last_fetched_id == "youtube:v3:full"
        assert source.last_fetched_item_at == repaired.item_at
