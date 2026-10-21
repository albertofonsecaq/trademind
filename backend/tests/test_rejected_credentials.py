"""
Tests for #54 — a connection marked active whose credentials are not.

#49 refused work a source could not do when the *stored state* said so. This is
the case the state does not know about: Telegram can invalidate a session from
the other side — the user terminates that device, two-factor is re-armed, the
account is deactivated — and nothing wrote that back. `status` stayed "active",
so Fetch now was offered, accepted, and the work dropped in the background.

The fix is one write: a failure that means our stored session is no longer usable
marks the connection it failed on. #49's gate then refuses the source on its own,
which is why there is no second check anywhere here.

The hard half is telling that apart from a failure that says nothing about the
credentials. Marking a connection on a rate limit or a network blip would pause
a healthy account — so each class of failure is pinned below, in both directions.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    ChannelPrivateError,
    FloodWaitError,
    ServerError,
    SessionExpiredError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UserDeactivatedError,
)

from app.connectors.base import FetchPosition, RawMessage
from app.models.platform_connection import PlatformConnection
from app.models.source_config import SourceConfig
from app.services import ingestion_pipeline
from app.services.connection_health import credentials_rejected
from tests.conftest import FakeSession


class _Request:
    """Stands in for the Telethon request an RPCError names as its cause."""


def _rpc(error_class):
    return error_class(request=_Request())


# Every one of these means the stored session will not work again until the user
# reconnects. They are not hypothetical: each is a distinct way Telegram ends a
# session from its side.
REJECTIONS = [
    AuthKeyUnregisteredError,       # the key is gone from Telegram's side
    AuthKeyDuplicatedError,         # used from two IPs; Telegram kills it
    SessionRevokedError,            # the user terminated this device
    SessionExpiredError,
    SessionPasswordNeededError,     # two-factor re-armed since we signed in
    UserDeactivatedError,           # the account itself is gone
]

# And these say nothing about our credentials.
INNOCENT = [
    lambda: FloodWaitError(request=_Request(), capture=30),
    lambda: ServerError(request=_Request(), message="Internal server error"),
    lambda: _rpc(ChannelPrivateError),      # this channel, not this account
    lambda: TimeoutError("no answer"),
    lambda: ConnectionError("network went away"),
    lambda: ValueError("a bug of ours"),
]


class TestTellingTheTwoApart:
    @pytest.mark.parametrize("error_class", REJECTIONS, ids=lambda c: c.__name__)
    def test_a_rejected_session_is_recognised(self, error_class):
        assert credentials_rejected(_rpc(error_class)) is True

    @pytest.mark.parametrize("make", INNOCENT, ids=range(len(INNOCENT)))
    def test_an_unrelated_failure_is_not(self, make):
        """
        The expensive mistake in the other direction: marking a connection on a
        rate limit would pause a working account until someone noticed.
        """
        assert credentials_rejected(make()) is False


# ── through a fetch ──────────────────────────────────────────────────────────

def _rows(*, status="active"):
    """A source, its connection, and a session that answers for both by id."""
    connection = SimpleNamespace(
        id=uuid.uuid4(), status=status, label="an account",
        platform="telegram", last_verified_at=None,
    )
    source = SimpleNamespace(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), source_type="telegram",
        identifier="chan", content_filters={"text": True},
        last_fetched_id=None, last_fetched_item_at=None, last_fetched_at=None,
        platform_connection_id=connection.id, connection=connection,
        is_paused=False,
    )
    db = FakeSession(rows={
        SourceConfig: {source.id: source},
        PlatformConnection: {connection.id: connection},
    })
    return SimpleNamespace(source=source, connection=connection, db=db)


class _Connector:
    """Yields what it was given, then fails the way it was told to."""

    def __init__(self, error, *, messages=()):
        self._error = error
        self._messages = list(messages)

    async def fetch_new(self, position: FetchPosition):
        for msg in self._messages:
            yield msg
        raise self._error

    async def fetch_range(self, date_start, date_end):
        for msg in self._messages:
            yield msg
        raise self._error

    def repaired_position(self):
        return None


def _a_message(rows, *, number: int) -> RawMessage:
    """
    A message the source's filters reject, so the position advances without any
    model call — this file is about the failure, not the pipeline.
    """
    return RawMessage(
        stable_id=f"telegram:chan:{number}",
        source_config_id=rows.source.id,
        workspace_id=rows.source.workspace_id,
        text=None, author=None, channel="chan",
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=number),
        content_type="video",          # filters allow text only
    )


async def _fetch(rows, error, monkeypatch, *, messages=()):
    monkeypatch.setattr(
        ingestion_pipeline, "_build_connector",
        lambda source: _Connector(error, messages=messages),
    )
    return await ingestion_pipeline.run_fetch_pipeline(
        rows.db,
        source=rows.source,
        workspace=SimpleNamespace(id=rows.source.workspace_id, topic_scope="forex"),
    )


class TestAFetchWhoseSessionIsGone:
    async def test_it_marks_the_connection(self, monkeypatch):
        rows = _rows()

        with pytest.raises(Exception):
            await _fetch(rows, _rpc(AuthKeyUnregisteredError), monkeypatch)

        assert rows.connection.status == "revoked"

    async def test_fetch_now_then_refuses_the_source(self, monkeypatch):
        """
        The point of writing the state rather than handling the error: #49's
        gate answers for this source now, with no second check anywhere. Asked
        through the endpoint, because that is the criterion — a 409 where the
        same request was accepted before the session died.
        """
        from fastapi import BackgroundTasks, HTTPException

        from app.api import sources as sources_api
        from app.models.workspace import Workspace

        rows = _rows()
        with pytest.raises(Exception):
            await _fetch(rows, _rpc(SessionRevokedError), monkeypatch)

        workspace = SimpleNamespace(
            id=rows.source.workspace_id, name="ws", topic_scope="forex",
            ingestion_paused=False,
        )
        rows.db.register(Workspace, workspace)

        with pytest.raises(HTTPException) as exc:
            await sources_api.trigger_fetch(
                workspace_id=workspace.id, source_id=rows.source.id,
                background_tasks=BackgroundTasks(),
                current_user=SimpleNamespace(id=uuid.uuid4()), db=rows.db,
            )

        assert exc.value.status_code == 409

    async def test_it_keeps_what_the_run_already_got_through(self, monkeypatch):
        """
        The session can die mid-stream. The position must survive it, or the next
        fetch after the user reconnects re-reads — and re-pays for — the lot.
        """
        rows = _rows()
        messages = [_a_message(rows, number=n) for n in (1, 2, 3)]

        with pytest.raises(Exception):
            await _fetch(rows, _rpc(SessionExpiredError), monkeypatch, messages=messages)

        assert rows.source.last_fetched_id == "telegram:chan:3"

    async def test_it_does_not_stamp_the_fetch_time(self, monkeypatch):
        """
        last_fetched_at drives the cadence. Stamping it on a failure would send
        the source quiet for a whole interval — the reasoning #9 left in place
        for the other fatal failures.
        """
        rows = _rows()

        with pytest.raises(Exception):
            await _fetch(
                rows, _rpc(SessionRevokedError), monkeypatch,
                messages=[_a_message(rows, number=1)],
            )

        assert rows.source.last_fetched_at is None


class TestAFetchThatFailedForOtherReasons:
    @pytest.mark.parametrize("make", INNOCENT, ids=range(len(INNOCENT)))
    async def test_the_connection_is_left_alone(self, make, monkeypatch):
        rows = _rows()

        with pytest.raises(Exception):
            await _fetch(rows, make(), monkeypatch)

        assert rows.connection.status == "active"


# ── through a backfill ───────────────────────────────────────────────────────

async def _backfill(rows, error, monkeypatch, *, messages=()):
    """
    The backfill runner, which reaches the failure by its own route: the job row
    has to come out of it readable, so the marking cannot be the fetch path's.
    """
    from app.api import sources as sources_api
    from app.models.backfill_job import BackfillJob
    from app.models.workspace import Workspace

    job = SimpleNamespace(
        id=uuid.uuid4(), source_config_id=rows.source.id,
        workspace_id=rows.source.workspace_id, status="queued",
        date_range_start=datetime(2026, 9, 1, tzinfo=timezone.utc), date_range_end=None,
        items_ingested=0, items_failed=0, error_message=None, completed_at=None,
    )
    rows.db.register(BackfillJob, job)
    rows.db.register(Workspace, SimpleNamespace(
        id=rows.source.workspace_id, topic_scope="forex", ingestion_paused=False,
    ))
    monkeypatch.setattr(
        ingestion_pipeline, "_build_connector",
        lambda source: _Connector(error, messages=messages),
    )
    # The runner opens its own session, as a background task must.
    monkeypatch.setattr(sources_api, "AsyncSessionLocal", lambda: rows.db)
    await sources_api._run_backfill_bg(job_id=job.id)
    return job


class TestABackfillWhoseSessionIsGone:
    async def test_it_marks_the_connection(self, monkeypatch):
        rows = _rows()

        await _backfill(rows, _rpc(AuthKeyUnregisteredError), monkeypatch)

        assert rows.connection.status == "revoked"

    async def test_the_job_says_what_to_do_in_our_own_words(self, monkeypatch):
        """
        Telethon's wording is "The key is not registered in the system (caused
        by GetHistoryRequest)", which names its own internals and tells the user
        nothing. A job row is read by a person.
        """
        rows = _rows()

        job = await _backfill(rows, _rpc(SessionRevokedError), monkeypatch)

        assert job.status == "failed"
        assert "reconnect" in job.error_message.lower()
        assert "caused by" not in job.error_message.lower()


class TestABackfillThatFailedForOtherReasons:
    @pytest.mark.parametrize("make", INNOCENT, ids=range(len(INNOCENT)))
    async def test_the_connection_is_left_alone(self, make, monkeypatch):
        rows = _rows()

        job = await _backfill(rows, make(), monkeypatch)

        assert rows.connection.status == "active"
        assert job.status == "failed"       # the job still failed, honestly
