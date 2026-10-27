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
from app.models.failed_message import FailedMessage
from app.models.workspace_member import WorkspaceMember
from tests.conftest import make_source, make_user, make_workspace

pytestmark = pytest.mark.db

_AT = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)


async def _member(session, workspace):
    user = await make_user(session)
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="member"))
    await session.flush()
    return user


async def _failed(session, source, msg_id, *, given_up=False, attempts=1, failed_at=_AT):
    row = FailedMessage(
        workspace_id=source.workspace_id, source_config_id=source.id,
        stable_id=f"telegram:chan:{msg_id}", message_at=_AT, error=f"broken {msg_id}",
        attempts=attempts, first_failed_at=failed_at, last_failed_at=failed_at,
        given_up_at=failed_at if given_up else None,
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
