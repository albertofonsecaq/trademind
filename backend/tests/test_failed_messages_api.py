"""
The failed messages of a source, through the API — #96.

The sources list says, per source, how many failed messages are still being
retried and how many were given up; a source's own list says which, and why,
so a person can find each one on the platform. Only members of the workspace
see them.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import sources as sources_api
from app.core.clock import now_utc
from app.core.config import settings
from app.models.failed_message import FailedMessage
from app.models.workspace_member import WorkspaceMember
from tests.conftest import fake_refusal_store, make_source, make_user, make_workspace

pytestmark = pytest.mark.db

_AT = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def cooldown_in_force(monkeypatch):
    """
    The cool-down a waiting message's wait is measured against (#141), with
    nothing refusing us: the base, an hour.

    Faked for every test here because a source's list of failed messages now
    reads it, and a refusal is a row of its own read in a session of its own
    (ADR 0009) — not the transaction these tests write their rows into.
    """
    monkeypatch.setattr(settings, "YOUTUBE_REFUSAL_COOLDOWN_MINUTES", 60)
    return fake_refusal_store(monkeypatch)


async def _member(session, workspace):
    user = await make_user(session)
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="member"))
    await session.flush()
    return user


async def _failed(session, source, msg_id, *, given_up=False, attempts=1, failed_at=_AT, retry_after=None):
    row = FailedMessage(
        workspace_id=source.workspace_id, source_config_id=source.id,
        stable_id=f"telegram:chan:{msg_id}", message_at=_AT, error=f"broken {msg_id}",
        attempts=attempts, first_failed_at=failed_at, last_failed_at=failed_at,
        given_up_at=failed_at if given_up else None, retry_after=retry_after,
    )
    session.add(row)
    await session.flush()
    return row


class TestTheSourcesList:
    async def test_counts_each_sources_failed_messages_by_whether_they_are_still_retried(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        busy, quiet = await make_source(db_session, workspace), await make_source(db_session, workspace)
        await _failed(db_session, busy, 1)
        await _failed(db_session, busy, 2)
        await _failed(db_session, busy, 3, given_up=True, attempts=3)

        listed = {s.id: s for s in await sources_api.list_sources(workspace.id, user, db_session)}

        assert (listed[busy.id].failed_retrying, listed[busy.id].failed_given_up) == (2, 1)
        assert (listed[quiet.id].failed_retrying, listed[quiet.id].failed_given_up) == (0, 0)


class TestWaitingMessages:
    """#132: a failed message whose retry is still ahead is waiting, and the page says until when."""

    async def test_are_counted_apart_from_those_retried_now_and_those_given_up(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        now = now_utc()
        await _failed(db_session, source, 1, attempts=0, retry_after=now + timedelta(hours=1))   # a refusal
        await _failed(db_session, source, 2, attempts=1, retry_after=now + timedelta(hours=23))  # no transcript
        await _failed(db_session, source, 3, attempts=1, retry_after=now - timedelta(minutes=1))  # its time has come
        await _failed(db_session, source, 4)
        await _failed(db_session, source, 5, given_up=True, attempts=3)

        [listed] = await sources_api.list_sources(workspace.id, user, db_session)

        assert (listed.failed_waiting, listed.failed_retrying, listed.failed_given_up) == (2, 2, 1)

    async def test_say_when_they_will_next_be_retried(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        until = now_utc() + timedelta(hours=1)
        await _failed(db_session, source, 1, attempts=0, retry_after=until)
        await _failed(db_session, source, 2)

        rows = await sources_api.list_failed_messages(workspace.id, source.id, user, db_session)
        listed = {m.stable_id: m for m in map(sources_api.FailedMessageOut.model_validate, rows)}

        assert listed["telegram:chan:1"].retry_after == until
        assert listed["telegram:chan:2"].retry_after is None


class TestASourcesFailedMessages:
    async def test_are_listed_with_why_and_how_often_newest_failure_first(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        await _failed(db_session, source, 1, given_up=True, attempts=3)
        await _failed(db_session, source, 2, failed_at=_AT + timedelta(hours=1))

        listed = await sources_api.list_failed_messages(workspace.id, source.id, user, db_session)

        assert [(m.stable_id, m.error, m.attempts, m.given_up_at is not None) for m in listed] == [
            ("telegram:chan:2", "broken 2", 1, False),
            ("telegram:chan:1", "broken 1", 3, True),
        ]
        assert listed[0].message_at == _AT

    async def test_of_another_workspaces_source_are_not_found(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        elsewhere = await make_source(db_session)

        with pytest.raises(HTTPException) as refused:
            await sources_api.list_failed_messages(workspace.id, elsewhere.id, user, db_session)

        assert refused.value.status_code == 404

    async def test_are_refused_to_someone_outside_the_workspace(self, db_session):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        stranger = await make_user(db_session)

        with pytest.raises(HTTPException) as refused:
            await sources_api.list_failed_messages(workspace.id, source.id, stranger, db_session)

        assert refused.value.status_code == 403


class TestSendingAGivenUpMessageBack:
    """#102: a person deals with a given-up message by sending it back to be retried."""

    async def test_one_is_retried_again_with_fresh_attempts(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        row = await _failed(db_session, source, 1, given_up=True, attempts=3)

        sent = await sources_api.send_back_failed_message(workspace.id, source.id, row.id, user, db_session)

        assert (sent.given_up_at, sent.attempts) == (None, 0)
        assert sent.error == "broken 1"
        [listed] = await sources_api.list_failed_messages(workspace.id, source.id, user, db_session)
        assert listed.given_up_at is None

    async def test_one_still_being_retried_is_left_as_it_is(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        row = await _failed(db_session, source, 1, attempts=2)

        sent = await sources_api.send_back_failed_message(workspace.id, source.id, row.id, user, db_session)

        assert sent.attempts == 2

    async def test_all_of_a_sources_given_up_messages_go_back_together(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        other = await make_source(db_session, workspace)
        await _failed(db_session, source, 1, given_up=True, attempts=3)
        await _failed(db_session, source, 2, given_up=True, attempts=3)
        await _failed(db_session, source, 3, attempts=2)
        await _failed(db_session, other, 4, given_up=True, attempts=3)

        sent = await sources_api.send_back_given_up(workspace.id, source.id, user, db_session)

        assert sent.sent_back == 2
        listed = {m.stable_id: m for m in await sources_api.list_failed_messages(workspace.id, source.id, user, db_session)}
        assert all(m.given_up_at is None for m in listed.values())
        assert (listed["telegram:chan:1"].attempts, listed["telegram:chan:2"].attempts) == (0, 0)
        assert listed["telegram:chan:3"].attempts == 2
        [elsewhere] = await sources_api.list_failed_messages(workspace.id, other.id, user, db_session)
        assert elsewhere.given_up_at is not None

    async def test_is_refused_to_someone_outside_the_workspace(self, db_session):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        row = await _failed(db_session, source, 1, given_up=True, attempts=3)
        stranger = await make_user(db_session)

        for attempt in (
            sources_api.send_back_failed_message(workspace.id, source.id, row.id, stranger, db_session),
            sources_api.send_back_given_up(workspace.id, source.id, stranger, db_session),
        ):
            with pytest.raises(HTTPException) as refused:
                await attempt
            assert refused.value.status_code == 403

    async def test_of_another_source_or_workspace_is_not_found(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        sibling = await make_source(db_session, workspace)
        elsewhere = await make_source(db_session)
        row = await _failed(db_session, sibling, 1, given_up=True, attempts=3)

        for attempt in (
            sources_api.send_back_failed_message(workspace.id, source.id, row.id, user, db_session),
            sources_api.send_back_given_up(workspace.id, elsewhere.id, user, db_session),
        ):
            with pytest.raises(HTTPException) as refused:
                await attempt
            assert refused.value.status_code == 404


class TestStuckMessages:
    """
    #141: a message waiting out one refusal after another is told apart from one
    that will go through at the time it shows. `failed_message_service.is_stuck`
    is the rule; these say the page is given it.
    """

    async def _source_with_both(self, session, workspace):
        source = await make_source(session, workspace)
        now = now_utc()
        # Seven days of refusals, no attempt ever counted: BlBoKnm9O_o's state.
        await _failed(session, source, 1, attempts=0,
                      failed_at=now - timedelta(days=7), retry_after=now + timedelta(hours=4))
        # Refused for the first time a minute ago, and waiting the hour that
        # refusal earned: an ordinary wait, and one cool-down long.
        await _failed(session, source, 2, attempts=0,
                      failed_at=now - timedelta(minutes=1), retry_after=now + timedelta(minutes=59))
        # Waiting a long time, but its own attempts are failing: it will be given up.
        await _failed(session, source, 3, attempts=1,
                      failed_at=now - timedelta(days=7), retry_after=now + timedelta(hours=23))
        await _failed(session, source, 4, given_up=True, attempts=3)
        return source

    async def test_are_counted_as_a_part_of_the_waiting_so_the_source_still_adds_up(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        await self._source_with_both(db_session, workspace)

        [listed] = await sources_api.list_sources(workspace.id, user, db_session)

        assert (listed.failed_waiting, listed.failed_stuck, listed.failed_given_up) == (3, 1, 1)
        assert listed.failed_retrying == 0

    async def test_a_source_with_none_is_counted_as_having_none(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await make_source(db_session, workspace)
        # Refused once, waiting exactly the hour that refusal earned.
        now = now_utc()
        await _failed(db_session, source, 1, attempts=0,
                      failed_at=now, retry_after=now + timedelta(hours=1))

        [listed] = await sources_api.list_sources(workspace.id, user, db_session)

        assert (listed.failed_waiting, listed.failed_stuck) == (1, 0)

    async def test_the_sources_own_list_says_which_message_is_stuck(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        source = await self._source_with_both(db_session, workspace)

        listed = {m.stable_id: m for m in await sources_api.list_failed_messages(workspace.id, source.id, user, db_session)}

        assert listed["telegram:chan:1"].stuck is True
        assert [listed[f"telegram:chan:{n}"].stuck for n in (2, 3, 4)] == [False, False, False]
