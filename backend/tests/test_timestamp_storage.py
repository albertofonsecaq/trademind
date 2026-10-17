"""
What the database actually does with our timestamps, today — #32, for #8.

Every timestamp column is `timestamptz`, and no model declares
`DateTime(timezone=True)`, so SQLAlchemy binds writes as TIMESTAMP WITHOUT TIME
ZONE. These tests record the consequences rather than the intention:

- the column really is `timestamptz`, which is the premise everything else rests
  on;
- a naive write round-trips and comes back aware, because the session's time zone
  is UTC and Postgres reads the naive input in it;
- an aware write is rejected outright, which is the DataError in #8.

All green now. #34 inverts the third one — that is the point: the behaviour
change will be visible as a diff here rather than asserted in a commit message.

These need a real database, so they are skipped unless one is configured. See
conftest.py for how to run them.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models.backfill_job import BackfillJob
from app.models.source_config import SourceConfig
from app.models.user import User
from app.models.workspace import Workspace

pytestmark = pytest.mark.db

# Microseconds on purpose: a round trip that silently truncated sub-second
# precision would still satisfy a whole-second instant.
AN_INSTANT = datetime(2026, 1, 22, 14, 30, 5, 123456, tzinfo=timezone.utc)


async def _a_source(session) -> SourceConfig:
    """
    A user, a workspace and a source — the minimum a backfill job needs.

    Flushed in turn rather than together: these three carry foreign keys to each
    other with no matching relationships, so the unit of work has no dependency
    to order the inserts by and would otherwise send a child first. (The job
    below is fine either way — a source does declare its backfill jobs.)
    """
    user = User(
        id=uuid.uuid4(),
        email=f"timestamps-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    session.add(user)
    await session.flush()

    workspace = Workspace(
        id=uuid.uuid4(), name="timestamp check", owner_user_id=user.id,
        topic_scope="forex",
    )
    session.add(workspace)
    await session.flush()

    source = SourceConfig(
        id=uuid.uuid4(), workspace_id=workspace.id, source_type="telegram",
        identifier="chan", created_by_user_id=user.id,
    )
    session.add(source)
    await session.flush()
    return source


def _a_job(source: SourceConfig, starting: datetime) -> BackfillJob:
    return BackfillJob(
        id=uuid.uuid4(),
        source_config_id=source.id,
        workspace_id=source.workspace_id,
        date_range_start=starting,
        triggered_by_user_id=source.created_by_user_id,
    )


class TestThePremise:
    async def test_the_column_stores_a_time_zone(self, db_session):
        """
        Everything in #8 follows from the schema and the mapping disagreeing, so
        the schema half is asserted rather than assumed.
        """
        column_type = (
            await db_session.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'backfill_jobs' "
                    "AND column_name = 'date_range_start'"
                )
            )
        ).scalar_one()

        assert column_type == "timestamp with time zone"


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
        job = _a_job(source, AN_INSTANT.replace(tzinfo=None))   # what the ORM binds today
        db_session.add(job)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert stored.tzinfo is not None     # timestamptz, so it comes back aware
        assert stored == AN_INSTANT          # same instant, microseconds included


class TestAnAwareWrite:
    async def test_is_rejected_today(self, db_session):
        """
        #8's error, pinned: the ORM binds TIMESTAMP WITHOUT TIME ZONE against a
        timestamptz column, so an aware value cannot be sent at all. This is the
        assertion #34 inverts — when it does, this class should assert the write
        succeeds, not that a different error is raised.
        """
        source = await _a_source(db_session)
        db_session.add(_a_job(source, AN_INSTANT))              # aware

        with pytest.raises(DBAPIError, match="offset-naive and offset-aware"):
            await db_session.flush()
