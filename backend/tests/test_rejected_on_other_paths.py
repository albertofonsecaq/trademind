"""
Tests for #57 — the two paths that touch Telegram outside a fetch.

#54 made a rejected session mark the connection it failed on, on the paths its
criteria named: the recurring fetch and the backfill. Two others talk to
Telegram and did not, so the same rejection left the row reading "active", the
source still being offered, and the user told something untrue:

- Resuming "from now" has to read the source's newest item. Every failure of
  that read became one 409 advising "try again, or resume with catch_up" —
  advice that cannot work when Telegram has ended the session, because both of
  those fail the same way.
- Listing a connection's channels built a client with nothing around it, so a
  rejected session was a 500. That is the path a user walks *deliberately* while
  adding a source, which makes it the likeliest place to find out a session is
  dead, and the worst place to show a stack trace.

What must not happen is the opposite mistake: a deleted channel head and an
unreachable platform are not reasons to revoke an account. So every test here
has its mirror image.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from telethon.errors import AuthKeyUnregisteredError, FloodWaitError, SessionRevokedError

from app.api import connections as connections_api
from app.api import sources as sources_api
from app.models.platform_connection import PlatformConnection
from app.models.source_config import SourceConfig
from app.services import source_resume_service as resume_service
from tests.conftest import FakeSession


class _Request:
    """Stands in for the Telethon request an RPCError names as its cause."""


def _rejection():
    return AuthKeyUnregisteredError(request=_Request())


def _transient():
    return FloodWaitError(request=_Request(), capture=30)


def _connection(status="active"):
    return SimpleNamespace(
        id=uuid.uuid4(), status=status, label="an account", platform="telegram",
        session_credential="x", auth_metadata={"api_id": 1, "api_hash": "h"},
    )


def _paused_source(connection):
    return SimpleNamespace(
        id=uuid.uuid4(), identifier="chan", source_type="telegram",
        fetch_cadence="hourly", content_filters={"text": True},
        platform_connection_id=connection.id, connection=connection,
        is_paused=True, paused_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        last_fetched_id="telegram:chan:500",
        last_fetched_item_at=datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc),
        last_fetched_at=datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc),
    )


# ── resuming "from now" ──────────────────────────────────────────────────────

async def _resume_from_now(monkeypatch, *, failing_with=None, latest=None):
    """Resume a paused source whose newest-item probe does what it is told."""
    connection = _connection()
    source = _paused_source(connection)

    class _Connector:
        async def latest_stable_id(self):
            if failing_with is not None:
                raise failing_with
            return latest

    monkeypatch.setattr(resume_service, "_build_connector", lambda s: _Connector())
    db = FakeSession(rows={
        SourceConfig: {source.id: source},
        PlatformConnection: {connection.id: connection},
    })
    with pytest.raises(HTTPException) as exc:
        await sources_api.resume_source(
            workspace_id=uuid.uuid4(), source_id=source.id,
            payload=sources_api.ResumeRequest(mode="from_now"),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
        )
    return SimpleNamespace(error=exc.value, source=source, connection=connection, db=db)


class TestResumeWhenTheSessionIsGone:
    async def test_it_marks_the_connection(self, monkeypatch):
        result = await _resume_from_now(monkeypatch, failing_with=_rejection())

        assert result.connection.status == "revoked"

    async def test_it_does_not_send_the_user_to_retry(self, monkeypatch):
        """
        The bug: "try again, or resume with catch_up" — and both of those fail
        identically while the session is dead.
        """
        result = await _resume_from_now(monkeypatch, failing_with=_rejection())
        detail = result.error.detail.lower()

        assert result.error.status_code == 409
        assert "reconnect" in detail
        assert "catch_up" not in detail and "try again" not in detail

    async def test_the_source_stays_paused(self, monkeypatch):
        """Resuming failed, so nothing about the pause may have moved."""
        result = await _resume_from_now(monkeypatch, failing_with=_rejection())

        assert result.source.is_paused is True
        assert result.source.last_fetched_id == "telegram:chan:500"


class TestResumeWhenItFailedForOtherReasons:
    async def test_a_transient_failure_leaves_the_connection_alone(self, monkeypatch):
        result = await _resume_from_now(monkeypatch, failing_with=_transient())

        assert result.connection.status == "active"

    async def test_a_transient_failure_still_says_try_again(self, monkeypatch):
        """The old advice is right for this one, so it must survive."""
        result = await _resume_from_now(monkeypatch, failing_with=_transient())

        assert result.error.status_code == 409
        assert "try again" in result.error.detail.lower()

    async def test_a_connector_that_knows_of_no_newest_item(self, monkeypatch):
        """
        Nothing to move the cursor to, which says nothing about the account —
        an empty channel, or one whose head was deleted.
        """
        result = await _resume_from_now(monkeypatch, latest=None)

        assert result.connection.status == "active"
        assert "try again" in result.error.detail.lower()


# ── listing a connection's channels ──────────────────────────────────────────

async def _list_dialogs(monkeypatch, *, failing_with):
    connection = _connection()
    db = FakeSession(rows={PlatformConnection: {connection.id: connection}})

    async def _raise(**_kwargs):
        raise failing_with

    monkeypatch.setattr("app.connectors.telegram.list_dialogs", _raise)
    monkeypatch.setattr(connections_api, "decrypt_credential", lambda _c: "session")
    return SimpleNamespace(connection=connection, db=db, call=lambda: (
        connections_api.list_telegram_dialogs(
            workspace_id=uuid.uuid4(), connection_id=connection.id,
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
        )
    ))


class TestListingChannelsWhenTheSessionIsGone:
    async def test_it_refuses_instead_of_crashing(self, monkeypatch):
        """A 500 told the user nothing and left the row looking usable."""
        listing = await _list_dialogs(monkeypatch, failing_with=_rejection())

        with pytest.raises(HTTPException) as exc:
            await listing.call()

        assert exc.value.status_code == 409
        assert "reconnect" in exc.value.detail.lower()

    async def test_it_marks_the_connection(self, monkeypatch):
        listing = await _list_dialogs(monkeypatch, failing_with=_rejection())

        with pytest.raises(HTTPException):
            await listing.call()

        assert listing.connection.status == "revoked"


class TestListingChannelsWhenItFailedForOtherReasons:
    async def test_the_failure_is_not_turned_into_a_revoked_account(self, monkeypatch):
        listing = await _list_dialogs(monkeypatch, failing_with=_transient())

        with pytest.raises(FloodWaitError):
            await listing.call()

        assert listing.connection.status == "active"


# ── and the mode that never probes at all ────────────────────────────────────

class TestCatchUpIsUntouched:
    async def test_it_does_not_reach_telegram_and_cannot_revoke_anything(self, monkeypatch):
        """
        CATCH_UP leaves the cursor alone, so it never builds a connector — the
        rejection handling must not have given it a reason to.
        """
        connection = _connection()
        source = _paused_source(connection)

        def _no_connector(_source):
            raise AssertionError("catch_up must not build a connector")

        monkeypatch.setattr(resume_service, "_build_connector", _no_connector)
        db = FakeSession(rows={
            SourceConfig: {source.id: source},
            PlatformConnection: {connection.id: connection},
        })

        await sources_api.resume_source(
            workspace_id=uuid.uuid4(), source_id=source.id,
            payload=sources_api.ResumeRequest(mode="catch_up"),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
        )

        assert source.is_paused is False
        assert connection.status == "active"
