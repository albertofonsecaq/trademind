"""
Reading back further than one page — #73, against a real database.

A hundred rows is a page, and the row after it has to be reachable or the list
is a cap with a nicer name. The page boundary is a record rather than a row
offset, so that a question asked while someone reads back through their history
cannot shift the pages underneath them.

That boundary is where the interesting failures live, and none of them are
visible to a fake: whether `(created_at, id) < (anchor, anchor_id)` is actually
exact, what happens to two records written in the same microsecond, and whether
the ordering the pages rely on is the ordering Postgres gives back. So this
file uses a real database and real rows, while the offline tests in
`test_ask_records.py` cover the shape of the query.

Needs a database, so it is skipped unless one is configured. See conftest.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api import ask as ask_api
from app.models.ask_record import AskRecord
from app.models.workspace_member import WorkspaceMember
from tests.conftest import make_user, make_workspace

pytestmark = pytest.mark.db

A_MONDAY = datetime(2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc)


async def _asker(session):
    """A user who is a member of their own workspace, as the API requires."""
    user = await make_user(session)
    workspace = await make_workspace(session, user=user)
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="owner",
    ))
    await session.flush()
    return user, workspace


async def _ask(session, user, workspace, question, asked_at):
    record = AskRecord(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id,
        question=question, answer=f"an answer to {question}", sources=[],
        language="en", created_at=asked_at,
    )
    session.add(record)
    await session.flush()
    return record


async def _page(session, user, workspace, *, limit, before_id=None):
    return await ask_api.list_ask_records(
        workspace_id=workspace.id, limit=limit, before_id=before_id,
        current_user=user, db=session,
    )


class TestPagingThroughTheHistory:
    async def test_every_question_is_reached_exactly_once(self, db_session):
        """
        Five records, two at a time. Walking the pages has to yield all five,
        newest first, with nothing repeated and nothing skipped — which is the
        whole claim a `before_id` boundary makes.
        """
        user, workspace = await _asker(db_session)
        for minute in range(5):
            await _ask(db_session, user, workspace, f"q{minute}", A_MONDAY + timedelta(minutes=minute))

        walked: list[str] = []
        before_id = None
        while True:
            page = await _page(db_session, user, workspace, limit=2, before_id=before_id)
            if not page:
                break
            walked.extend(row.question for row in page)
            before_id = page[-1].id

        assert walked == ["q4", "q3", "q2", "q1", "q0"]

    async def test_two_questions_asked_in_the_same_microsecond_do_not_collide(self, db_session):
        """
        The timestamps tie, so the boundary falls to the id. Without that tie
        breaker one of these is served twice and the other never — the failure
        a fake cannot show, because it never compares two real rows.
        """
        user, workspace = await _asker(db_session)
        for name in ("tie-a", "tie-b"):
            await _ask(db_session, user, workspace, name, A_MONDAY)

        first = await _page(db_session, user, workspace, limit=1)
        second = await _page(db_session, user, workspace, limit=1, before_id=first[0].id)
        assert second, "the record tied on created_at was unreachable"
        third = await _page(db_session, user, workspace, limit=1, before_id=second[0].id)

        assert {first[0].question, second[0].question} == {"tie-a", "tie-b"}
        assert third == []

    async def test_a_question_asked_mid_walk_does_not_shift_the_pages(self, db_session):
        """
        An offset would: inserting a newer row pushes everything down by one,
        so the next page repeats a row the reader has already seen. Anchored on
        a record, the older pages do not move.
        """
        user, workspace = await _asker(db_session)
        for minute in range(4):
            await _ask(db_session, user, workspace, f"q{minute}", A_MONDAY + timedelta(minutes=minute))

        first = await _page(db_session, user, workspace, limit=2)
        assert [row.question for row in first] == ["q3", "q2"]

        await _ask(db_session, user, workspace, "asked-just-now", A_MONDAY + timedelta(hours=1))

        rest = await _page(db_session, user, workspace, limit=2, before_id=first[-1].id)
        assert [row.question for row in rest] == ["q1", "q0"]


class TestThePagesAreOnlyEverTheCallersOwn:
    async def test_another_members_questions_are_not_in_them(self, db_session):
        """
        Both users are members of the same workspace. An ask record is a
        personal layer, so membership is not access to the other's questions.
        """
        mine, workspace = await _asker(db_session)
        theirs = await make_user(db_session)
        db_session.add(WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=theirs.id, role="member",
        ))
        await db_session.flush()

        await _ask(db_session, mine, workspace, "mine", A_MONDAY)
        await _ask(db_session, theirs, workspace, "theirs", A_MONDAY + timedelta(minutes=1))

        page = await _page(db_session, mine, workspace, limit=100)

        assert [row.question for row in page] == ["mine"]

    async def test_another_members_record_cannot_anchor_a_page(self, db_session):
        """
        The anchor is looked up as the caller's own, so a record they do not own
        is not found rather than used — otherwise the boundary itself leaks that
        the record exists.
        """
        from fastapi import HTTPException

        mine, workspace = await _asker(db_session)
        theirs = await make_user(db_session)
        db_session.add(WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=theirs.id, role="member",
        ))
        await db_session.flush()
        not_mine = await _ask(db_session, theirs, workspace, "theirs", A_MONDAY)

        with pytest.raises(HTTPException) as refused:
            await _page(db_session, mine, workspace, limit=2, before_id=not_mine.id)

        assert refused.value.status_code == 404
