"""
What the database actually does with our timestamps, today — #32, for #8.

Every timestamp column is `timestamptz`, and no model declares
`DateTime(timezone=True)`, so SQLAlchemy binds writes as TIMESTAMP WITHOUT TIME
ZONE. These tests record the consequences rather than the intention:

- a naive value round-trips, and comes back aware, because the session's time
  zone is UTC and Postgres interprets the naive input in it;
- an aware value is rejected outright, which is the DataError in #8.

Both are green now. #34 and the batches after it invert the second one — that is
the point: the behaviour change will be visible as a diff here, rather than
asserted in a commit message.

These need a real database, so they are skipped unless one is configured. See
conftest.py for how to run them.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models.backfill_job import BackfillJob
from app.models.source_config import SourceConfig
from app.models.user import User
from app.models.workspace import Workspace

pytestmark = pytest.mark.db

AN_INSTANT = datetime(2026, 1, 22, 14, 30, 5, tzinfo=timezone.utc)


async def _a_source(db) -> SourceConfig:
    """
    A user, a workspace and a source — the minimum a backfill job needs.

    Flushed in turn rather than together: these models carry foreign keys
    without matching relationships, so the unit of work has no dependency to
    order the inserts by and would otherwise send the child first.
    """
    user = User(
        id=uuid.uuid4(),
        email=f"timestamps-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    db.add(user)
    await db.flush()

    workspace = Workspace(
        id=uuid.uuid4(), name="timestamp check", owner_user_id=user.id,
        topic_scope="forex",
    )
    db.add(workspace)
    await db.flush()

    source = SourceConfig(
        id=uuid.uuid4(), workspace_id=workspace.id, source_type="telegram",
        identifier="chan", created_by_user_id=user.id,
    )
    db.add(source)
    await db.flush()
    return source


class TestTheSessionsTimeZone:
    async def test_is_utc(self, db_session):
        """
        The assumption nobody wrote down: naive writes are correct *only*
        because Postgres reads them as UTC. If this ever changes, every stored
        timestamp shifts silently — so it fails here instead.
        """
        tz = (await db_session.execute(text("SHOW TimeZone"))).scalar_one()

        assert tz in ("UTC", "Etc/UTC")


class TestANaiveWrite:
    async def test_round_trips_without_losing_the_instant(self, db_session):
        source = await _a_source(db_session)
        naive = AN_INSTANT.replace(tzinfo=None)          # what the ORM binds today

        job = BackfillJob(
            id=uuid.uuid4(), source_config_id=source.id,
            workspace_id=source.workspace_id, date_range_start=naive,
            triggered_by_user_id=source.created_by_user_id,
        )
        db_session.add(job)
        await db_session.flush()
        db_session.expunge_all()                         # force a real read back

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert stored.tzinfo is not None                 # timestamptz, so aware
        assert stored == AN_INSTANT                      # and the same instant

    async def test_comes_back_as_the_same_instant_in_another_offset(self, db_session):
        """
        The round trip is about instants, not wall clocks: 14:30Z is 09:30 in
        New York, and the value that comes back must compare equal to both.
        """
        source = await _a_source(db_session)
        job = BackfillJob(
            id=uuid.uuid4(), source_config_id=source.id,
            workspace_id=source.workspace_id,
            date_range_start=AN_INSTANT.replace(tzinfo=None),
            triggered_by_user_id=source.created_by_user_id,
        )
        db_session.add(job)
        await db_session.flush()
        db_session.expunge_all()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert stored == AN_INSTANT.astimezone(timezone(timedelta(hours=-5)))


class TestAnAwareWrite:
    async def test_is_rejected_today(self, db_session):
        """
        #8's DataError, pinned: the ORM binds TIMESTAMP WITHOUT TIME ZONE
        against a timestamptz column, so an aware value cannot be sent at all.
        This is the assertion #34 inverts.
        """
        source = await _a_source(db_session)
        job = BackfillJob(
            id=uuid.uuid4(), source_config_id=source.id,
            workspace_id=source.workspace_id,
            date_range_start=AN_INSTANT,                 # aware
            triggered_by_user_id=source.created_by_user_id,
        )
        db_session.add(job)

        with pytest.raises(Exception) as exc:
            await db_session.flush()

        assert "offset-naive and offset-aware" in str(exc.value)
