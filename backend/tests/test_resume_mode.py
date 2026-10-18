"""
Tests for #12 — resuming a paused source asks how, and neither answer loses data.

Two failure modes matter here:

- a silent resume. A source paused for an hour and one paused for three weeks
  cost wildly different amounts to catch up on, so the mode has to be an
  explicit part of the request, with no back door that resumes without one.
- "resume from now" implemented as a reset. It may only move the fetch cursor
  forward past content that was never fetched; it must never delete or rewrite
  what the workspace already holds.

Offline like the rest of the suite: the session and the connector are stubbed.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import sources as sources_api
from app.services import source_resume_service as resume_service
from app.services.source_resume_service import NewestEvidenceUnknown


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """
    Returns queued rows in order, and records every write it is asked for.

    `evidence` stands in for what the workspace already holds, so a test can
    compare the count before and after. `statements` catches the hole a
    delete-recording stub leaves open: a bulk `execute(delete(EvidenceItem))`
    writes nothing through .delete() and would otherwise pass unnoticed.
    """

    def __init__(self, rows, evidence=3):
        self._rows = list(rows)
        self.added: list = []
        self.deleted: list = []
        self.statements: list[str] = []
        self.evidence = evidence
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(str(stmt).lower())
        return _Result(self._rows.pop(0) if self._rows else None)

    def wrote_anything_destructive(self) -> bool:
        return any(
            verb in stmt for stmt in self.statements for verb in ("delete", "update")
        )

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass


CURSOR = "telegram:chan:500"


def _paused_source() -> object:
    from types import SimpleNamespace
    return SimpleNamespace(
        id=uuid.uuid4(),
        identifier="chan",
        source_type="telegram",
        fetch_cadence="hourly",
        content_filters={"text": True},
        platform_connection_id=uuid.uuid4(),
        connection=SimpleNamespace(status="active"),
        is_paused=True,
        paused_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        last_fetched_id=CURSOR,
        # Aware, like the columns they stand in for (ADR 1): a naive fixture
        # here would raise the moment it met a value the service wrote.
        last_fetched_item_at=datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc),
        last_fetched_at=datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def connector(monkeypatch):
    """Stubs the connector the resume path builds, and what it reports as newest."""

    def _with_latest(latest):
        class _Connector:
            async def latest_stable_id(self):
                return latest

        monkeypatch.setattr(resume_service, "_build_connector", lambda source: _Connector())

    return _with_latest


async def _resume(source, mode, db=None):
    db = db or _Session([object(), source])      # membership row, then the source
    await sources_api.resume_source(
        workspace_id=uuid.uuid4(),
        source_id=source.id,
        payload=sources_api.ResumeRequest(mode=mode),
        current_user=type("U", (), {"id": uuid.uuid4()})(),
        db=db,
    )
    return db


# ── the mode is explicit, with no way around it ───────────────────────────────

class TestResumeModeIsExplicit:
    async def test_patch_cannot_unpause_without_choosing(self):
        """The back door: PATCH is_paused=false would be a silent default."""
        source = _paused_source()
        db = _Session([object(), source])

        with pytest.raises(HTTPException) as exc:
            await sources_api.update_source(
                workspace_id=uuid.uuid4(),
                source_id=source.id,
                payload=sources_api.SourceUpdateRequest(is_paused=False),
                current_user=type("U", (), {"id": uuid.uuid4()})(),
                db=db,
            )

        assert exc.value.status_code == 400
        assert source.is_paused is True           # still paused

    def test_a_mode_is_required(self):
        with pytest.raises(ValidationError):
            sources_api.ResumeRequest()

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(ValidationError):
            sources_api.ResumeRequest(mode="whatever")


# ── catch up: the cursor stays exactly where the pause left it ────────────────

class TestCatchUp:
    async def test_leaves_the_cursor_where_it_stopped(self, connector):
        connector(None)                           # never consulted in this mode
        source = _paused_source()

        await _resume(source, "catch_up")

        assert source.last_fetched_id == CURSOR
        assert source.is_paused is False
        assert source.paused_at is None

    async def test_removes_nothing(self, connector):
        connector(None)
        source = _paused_source()
        db = _Session([object(), source], evidence=3)

        await _resume(source, "catch_up", db=db)

        assert db.evidence == 3                   # the workspace's evidence is untouched
        assert db.deleted == []
        assert db.added == []
        assert not db.wrote_anything_destructive()


# ── resume from now: the cursor moves forward, and only forward ───────────────

class TestResumeFromNow:
    async def test_advances_the_cursor_to_the_newest_evidence(self, connector):
        connector("telegram:chan:900")
        source = _paused_source()

        await _resume(source, "from_now")

        assert source.last_fetched_id == "telegram:chan:900"
        assert source.is_paused is False

    async def test_removes_nothing(self, connector):
        connector("telegram:chan:900")
        source = _paused_source()
        db = _Session([object(), source], evidence=3)

        await _resume(source, "from_now", db=db)

        assert db.evidence == 3                   # declining a backlog is not deleting it
        assert db.deleted == []
        assert db.added == []
        assert not db.wrote_anything_destructive()

    async def test_never_clears_the_cursor_when_the_newest_is_unknown(self, connector):
        """
        An empty channel, or a connector that cannot say: clearing the cursor
        here would make the next poll re-ingest the channel's whole history.
        """
        connector(None)
        source = _paused_source()

        with pytest.raises(HTTPException) as exc:
            await _resume(source, "from_now")

        assert exc.value.status_code == 409
        assert source.last_fetched_id == CURSOR
        assert source.is_paused is True           # stays paused, nothing guessed


# ── what the connectors report as "newest" ────────────────────────────────────

class TestConnectorsReportTheirNewestItem:
    """
    FROM_NOW is only as safe as this id. It has to be the newest item and it has
    to be shaped like a cursor the connector's own fetch_new can read back.
    """

    def _telegram(self, messages):
        from app.connectors.telegram import TelegramConnector

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def iter_messages(self, entity, limit=None, **kw):
                async def gen():
                    for m in (messages[:limit] if limit else messages):
                        yield m
                return gen()

        c = TelegramConnector(
            source_config_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            channel_identifier="123/456",
            api_id=1,
            api_hash="x",
            session_string="x",
            content_filters={"text": True},
        )
        c._resolve_identifier = lambda: "entity"
        c._client = lambda: _Client()
        return c

    async def test_telegram_reports_the_newest_message(self):
        from types import SimpleNamespace
        # iter_messages is newest-first, so the first item is the newest
        connector = self._telegram([SimpleNamespace(id=900), SimpleNamespace(id=42)])

        assert await connector.latest_stable_id() == "telegram:123/456:900"

    async def test_telegram_reports_nothing_for_an_empty_channel(self):
        connector = self._telegram([])

        assert await connector.latest_stable_id() is None

    async def test_youtube_reports_the_newest_video(self, monkeypatch):
        from app.connectors.youtube import YouTubeConnector

        connector = YouTubeConnector(
            source_config_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            channel_identifier="@chan",
        )

        async def _videos():
            return [{"video_id": "new1"}, {"video_id": "old1"}]

        monkeypatch.setattr(connector, "_get_videos", _videos)

        # the suffix only has to be a shape fetch_new can read the id out of
        assert await connector.latest_stable_id() == "youtube:new1:full"

    async def test_youtube_reports_nothing_for_an_empty_channel(self, monkeypatch):
        from app.connectors.youtube import YouTubeConnector

        connector = YouTubeConnector(
            source_config_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            channel_identifier="@chan",
        )

        async def _videos():
            return []

        monkeypatch.setattr(connector, "_get_videos", _videos)

        assert await connector.latest_stable_id() is None


class TestResumeFromNowNeverGoesBackwards:
    """
    The cursor is the only thing from_now may move, and only forward. A deleted
    channel head, or a cursor a backfill pushed ahead, can make the "newest"
    item report behind it — writing that would re-walk the window the user just
    declined.
    """

    async def test_keeps_a_cursor_that_is_already_ahead(self, connector):
        connector("telegram:chan:120")            # behind the stored 500
        source = _paused_source()

        await _resume(source, "from_now")

        assert source.last_fetched_id == CURSOR
        assert source.is_paused is False          # still resumed, just not rewound

    async def test_takes_the_newest_when_it_is_ahead(self, connector):
        connector("telegram:chan:501")
        source = _paused_source()

        await _resume(source, "from_now")

        assert source.last_fetched_id == "telegram:chan:501"

    async def test_records_the_declined_window_so_it_is_never_re_fetched(self, connector):
        """
        The position's timestamp marks the far edge of what was declined. If the
        marker this resume wrote is later deleted, the connector falls back to
        this — not to the newest stored evidence, which sits behind the decline
        and would re-fetch, and re-pay for, the skipped window (#19).
        """
        connector("telegram:chan:900")
        source = _paused_source()
        before = source.last_fetched_item_at

        await _resume(source, "from_now")

        assert source.last_fetched_item_at > before

    async def test_moves_the_fetch_timestamp_so_the_backlog_is_not_re_offered(self, connector):
        """
        The prompt measures the backlog from last_fetched_at. Leaving it stale
        would offer the same declined weeks again on the next pause.
        """
        connector("telegram:chan:900")
        source = _paused_source()
        before = source.last_fetched_at

        await _resume(source, "from_now")

        assert source.last_fetched_at > before


class TestUnreachableSource:
    async def test_a_connector_that_cannot_be_built_leaves_the_source_paused(self, monkeypatch):
        """A revoked connection is not a reason to guess a cursor — or to catch up."""
        def _explode(source):
            raise RuntimeError("no active platform connection")

        monkeypatch.setattr(resume_service, "_build_connector", _explode)
        source = _paused_source()

        with pytest.raises(NewestEvidenceUnknown):
            await resume_service.resume_source(source=source, mode="from_now")

        assert source.is_paused is True
        assert source.last_fetched_id == CURSOR
