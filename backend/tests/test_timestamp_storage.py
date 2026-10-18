"""
What the database actually does with our timestamps, today — #32, for #8.

Every timestamp column is `timestamptz`, and no model declares
`DateTime(timezone=True)`, so SQLAlchemy binds writes as TIMESTAMP WITHOUT TIME
ZONE. These tests record the consequences rather than the intention:

- the column really is `timestamptz`, which is the premise everything else rests
  on;
- an aware write round-trips and comes back as the same instant, which is what
  #34 made true: the models now declare `DateTime(timezone=True)`, so a value
  carrying its time zone can be written at all;
- a naive write is now read in the *server's local* time zone, not as UTC. That
  is the sharp edge of this change and the reason nothing above the connectors
  may produce one (ADR 1), and the reason the API normalizes what clients send.

The second assertion in this file used to be that an aware write *failed* —
#8's DataError. #34 inverted it, which is exactly what this file is for.

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


class TestAnAwareWrite:
    async def test_round_trips_without_losing_the_instant(self, db_session):
        """
        The #8 failure, now the normal path: this exact write raised
        `DataError: can't subtract offset-naive and offset-aware datetimes`
        before #34, because the mapping bound it as TIMESTAMP WITHOUT TIME ZONE.
        """
        source = await _a_source(db_session)
        job = _a_job(source, AN_INSTANT)                        # aware, unconverted
        db_session.add(job)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert stored.tzinfo is not None     # timestamptz, so it comes back aware
        assert stored == AN_INSTANT          # same instant, microseconds included

    async def test_survives_an_offset_that_is_not_utc(self, db_session):
        """
        An aware value need not be UTC — a client could send +05:30 — and what
        is stored is the instant, not the wall clock it was written in.
        """
        source = await _a_source(db_session)
        in_kolkata = AN_INSTANT.astimezone(timezone(timedelta(hours=5, minutes=30)))
        job = _a_job(source, in_kolkata)
        db_session.add(job)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert stored == AN_INSTANT


class TestANaiveWrite:
    async def test_no_longer_means_utc(self, db_session):
        """
        The hazard this change introduces, pinned so nobody rediscovers it in
        production. Before #34 the mapping bound naive and Postgres read it in
        the session's time zone, so naive meant UTC. Now the column is declared
        aware, and the driver reads a naive value in the *machine's* local time
        zone — so the same value stores a different instant depending on where
        the process runs.

        Worse than a constant offset: the value is read with the local zone's
        rules *for its own date*, so a January timestamp written on a machine
        currently in summer time shifts by the winter offset. Measured here
        rather than assumed, because assuming it got this wrong first.

        Nothing above the connectors may produce one. The API normalizes what
        clients send (see TestTheApiBoundary), column defaults use now_utc, and
        connectors hand out aware values.
        """
        source = await _a_source(db_session)
        job = _a_job(source, AN_INSTANT.replace(tzinfo=None))
        db_session.add(job)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        # The offset local rules give *that date*, not today's.
        naive = AN_INSTANT.replace(tzinfo=None)
        local_offset = naive.astimezone().utcoffset()

        assert stored == AN_INSTANT - local_offset       # shifted by exactly that much
        if local_offset:                                  # and so wrong, unless UTC
            assert stored != AN_INSTANT


class TestTheApiBoundary:
    async def test_a_payload_without_an_offset_stores_the_same_instant_as_one_with(
        self, db_session
    ):
        """
        What protects the hazard above: a client sending `...T14:30:05` and one
        sending `...T14:30:05+00:00` must store the same instant, whatever the
        server's local time zone is.
        """
        from app.api.sources import BackfillRequest

        source = await _a_source(db_session)
        without_offset = BackfillRequest(date_range_start="2026-01-22T14:30:05.123456")
        with_offset = BackfillRequest(date_range_start="2026-01-22T14:30:05.123456+00:00")

        stored = []
        for payload in (without_offset, with_offset):
            job = _a_job(source, payload.date_range_start)
            db_session.add(job)
            await db_session.flush()
            stored.append(
                (
                    await db_session.execute(
                        select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
                    )
                ).scalar_one()
            )

        assert stored[0] == stored[1] == AN_INSTANT
