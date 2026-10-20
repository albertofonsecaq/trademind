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
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.models.backfill_job import BackfillJob
from app.models.embedding_row import EmbeddingRow
from app.models.evidence_item import EvidenceItem
from app.models.platform_connection import PlatformConnection
from app.models.source_config import SourceConfig
from app.models.trade_idea import TradeIdea
from app.models.user import User
from app.models.workspace import Workspace
from tests.conftest import make_card, make_job, make_source, make_workspace

pytestmark = pytest.mark.db

# Microseconds on purpose: a round trip that silently truncated sub-second
# precision would still satisfy a whole-second instant.
AN_INSTANT = datetime(2026, 1, 22, 14, 30, 5, 123456, tzinfo=timezone.utc)


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
        source = await make_source(db_session)
        job = make_job(source, AN_INSTANT)                        # aware, unconverted
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
        source = await make_source(db_session)
        in_kolkata = AN_INSTANT.astimezone(timezone(timedelta(hours=5, minutes=30)))
        job = make_job(source, in_kolkata)
        db_session.add(job)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert stored == AN_INSTANT


class TestANaiveWrite:
    async def test_no_longer_means_utc(self, db_session, monkeypatch):
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

        The zone is pinned for the duration of this test. Left to the machine,
        this asserts nothing at all wherever TZ=UTC — which is where CI will run
        it, so the one test documenting the hazard would be the one test that
        could never fail.

        Nothing above the connectors may produce one. The API reads what clients
        send as UTC (see TestTheApiBoundary), column defaults use now_utc, and
        connectors hand out aware values. Pinning the hazard is the record of
        why; #37 is where the convention gets something that *prevents* it.
        """
        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        source = await make_source(db_session)
        job = make_job(source, AN_INSTANT.replace(tzinfo=None))
        db_session.add(job)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BackfillJob.date_range_start).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        # New York in January is UTC-5, whatever the machine's own zone is.
        assert stored == AN_INSTANT + timedelta(hours=5)
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

        source = await make_source(db_session)
        without_offset = BackfillRequest(date_range_start="2026-01-22T14:30:05.123456")
        with_offset = BackfillRequest(date_range_start="2026-01-22T14:30:05.123456+00:00")

        stored = []
        for payload in (without_offset, with_offset):
            job = make_job(source, payload.date_range_start)
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


class TestEveryIngestionColumnRoundTrips:
    """
    One column proved the fix; these prove the sweep. #34 declared 16 columns
    aware across six models, and the dangerous half of that is the write side:
    a column that accepts an aware value but whose own *default* still produces
    a naive one would store a shifted instant on every insert, silently. That
    cannot be checked by reading the models — only by writing a row and reading
    it back.
    """

    async def test_evidence_and_the_idea_drawn_from_it(self, db_session):
        source = await make_source(db_session)
        evidence = EvidenceItem(
            id=uuid.uuid4(), workspace_id=source.workspace_id,
            source_config_id=source.id, stable_id=f"telegram:chan:{uuid.uuid4().hex[:6]}",
            source_type="telegram", type="text", content="EURUSD breakout",
            confidence=1.0, message_timestamp=AN_INSTANT,
        )
        db_session.add(evidence)
        await db_session.flush()

        idea = TradeIdea(
            id=uuid.uuid4(), evidence_item_id=evidence.id,
            workspace_id=source.workspace_id, message_timestamp=AN_INSTANT,
        )
        db_session.add(idea)
        await db_session.flush()

        for model, row in ((EvidenceItem, evidence), (TradeIdea, idea)):
            stored, created = (
                await db_session.execute(
                    select(model.message_timestamp, model.created_at).where(model.id == row.id)
                )
            ).one()
            assert stored == AN_INSTANT, model.__tablename__
            # created_at came from the model's own default, not from this test
            assert created.tzinfo is not None, model.__tablename__
            assert abs(created - datetime.now(timezone.utc)) < timedelta(minutes=5)

    async def test_a_platform_connection(self, db_session):
        source = await make_source(db_session)
        connection = PlatformConnection(
            id=uuid.uuid4(), workspace_id=source.workspace_id, platform="telegram",
            label="an account", connected_by_user_id=source.created_by_user_id,
            status="active", connected_at=AN_INSTANT, last_verified_at=AN_INSTANT,
        )
        db_session.add(connection)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(PlatformConnection.connected_at, PlatformConnection.last_verified_at)
                .where(PlatformConnection.id == connection.id)
            )
        ).one()

        assert stored == (AN_INSTANT, AN_INSTANT)

    async def test_every_source_timestamp(self, db_session):
        source = await make_source(db_session)
        source.backfill_start_date = AN_INSTANT
        source.paused_at = AN_INSTANT
        source.last_fetched_item_at = AN_INSTANT
        source.last_fetched_at = AN_INSTANT
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(
                    SourceConfig.backfill_start_date, SourceConfig.paused_at,
                    SourceConfig.last_fetched_item_at, SourceConfig.last_fetched_at,
                    SourceConfig.created_at,
                ).where(SourceConfig.id == source.id)
            )
        ).one()

        assert stored[:4] == (AN_INSTANT,) * 4
        assert stored[4].tzinfo is not None      # the model's default

    async def test_an_embedding_row(self, db_session):
        """Only a created_at, and it comes entirely from the model's default."""
        source = await make_source(db_session)
        row = EmbeddingRow(
            id=uuid.uuid4(), workspace_id=source.workspace_id, source_type="telegram",
            source_id=str(uuid.uuid4()), text="EURUSD breakout",
            embedding=[0.0] * settings.EMBEDDING_DIM,
        )
        db_session.add(row)
        await db_session.flush()

        created = (
            await db_session.execute(
                select(EmbeddingRow.created_at).where(EmbeddingRow.id == row.id)
            )
        ).scalar_one()

        assert created.tzinfo is not None
        assert abs(created - datetime.now(timezone.utc)) < timedelta(minutes=5)

    async def test_a_backfill_jobs_own_default(self, db_session):
        source = await make_source(db_session)
        job = make_job(source, AN_INSTANT)
        db_session.add(job)
        await db_session.flush()

        created = (
            await db_session.execute(
                select(BackfillJob.created_at).where(BackfillJob.id == job.id)
            )
        ).scalar_one()

        assert created.tzinfo is not None
        assert abs(created - datetime.now(timezone.utc)) < timedelta(minutes=5)


class TestEveryKnowledgeColumnRoundTrips:
    """
    #35's twelve columns, written and read back against a real database.

    The same reason as the ingestion batch above: the dangerous half of
    declaring a column aware is a `default=` or `onupdate=` still producing a
    naive value, which stores a shifted instant on every write and cannot be
    seen by reading the models.

    These tables are empty in the live database, so unlike #34 this batch had no
    stored rows to put at risk — but the writes it enables start now.
    """

    async def _a_card(self, session):
        """Returns the card and the user who owns the personal rows below it."""
        from app.models.strategy_card import StrategyCard

        source = await make_source(session)
        card = StrategyCard(
            id=uuid.uuid4(), workspace_id=source.workspace_id,
            setup_type="breakout", symbol_scope="EURUSD",
            description_en="a breakout", description_es="una ruptura",
            flowchart_spec={}, supporting_evidence=[], sample_size=0,
            source_count=0, version=1, version_history=[],
            validation_updated_at=AN_INSTANT, last_updated=AN_INSTANT,
        )
        session.add(card)
        await session.flush()
        return card, source.created_by_user_id

    async def test_a_strategy_card(self, db_session):
        from app.models.strategy_card import StrategyCard

        card, _ = await self._a_card(db_session)

        stored = (
            await db_session.execute(
                select(
                    StrategyCard.validation_updated_at, StrategyCard.last_updated,
                    StrategyCard.created_at,
                ).where(StrategyCard.id == card.id)
            )
        ).one()

        assert stored[:2] == (AN_INSTANT, AN_INSTANT)
        assert stored[2].tzinfo is not None                  # the model's own default

    async def test_an_outcome_check(self, db_session):
        from app.models.outcome_check import OutcomeCheck
        from app.models.trade_idea import TradeIdea
        from app.models.evidence_item import EvidenceItem

        source = await make_source(db_session)
        evidence = EvidenceItem(
            id=uuid.uuid4(), workspace_id=source.workspace_id,
            source_config_id=source.id, stable_id=f"telegram:chan:{uuid.uuid4().hex[:6]}",
            source_type="telegram", type="text", content="x", confidence=1.0,
        )
        db_session.add(evidence)
        await db_session.flush()
        idea = TradeIdea(
            id=uuid.uuid4(), evidence_item_id=evidence.id,
            workspace_id=source.workspace_id,
        )
        db_session.add(idea)
        await db_session.flush()

        check = OutcomeCheck(
            id=uuid.uuid4(), trade_idea_id=idea.id,
            workspace_id=source.workspace_id, window_days=30,
        )
        db_session.add(check)
        await db_session.flush()

        checked_at = (
            await db_session.execute(
                select(OutcomeCheck.checked_at).where(OutcomeCheck.id == check.id)
            )
        ).scalar_one()

        assert checked_at.tzinfo is not None
        assert abs(checked_at - datetime.now(timezone.utc)) < timedelta(minutes=5)

    async def test_a_plan_item_including_its_onupdate(self, db_session):
        """
        `onupdate=` is the one a model sweep is most likely to miss: it fires on
        every write after the first, so a naive one would shift an instant each
        time the row is touched.
        """
        from app.models.plan_item import PlanItem

        card, user_id = await self._a_card(db_session)
        item = PlanItem(
            id=uuid.uuid4(), strategy_card_id=card.id,
            user_id=user_id, status="watching",
        )
        db_session.add(item)
        await db_session.flush()

        item.status = "trading"
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(PlanItem.created_at, PlanItem.updated_at).where(PlanItem.id == item.id)
            )
        ).one()

        assert stored[0].tzinfo is not None and stored[1].tzinfo is not None
        assert abs(stored[1] - datetime.now(timezone.utc)) < timedelta(minutes=5)

    async def test_a_journal_entry_and_its_outcome(self, db_session):
        from app.models.journal_entry import JournalEntry
        from app.models.journal_outcome import JournalOutcome

        card, user_id = await self._a_card(db_session)
        entry = JournalEntry(
            id=uuid.uuid4(), user_id=user_id, symbol="EURUSD",
            action="long", entry=1, size=1, mode="paper", timestamp=AN_INSTANT,
        )
        db_session.add(entry)
        await db_session.flush()

        outcome = JournalOutcome(
            id=uuid.uuid4(), journal_entry_id=entry.id, user_id=entry.user_id,
            symbol="EURUSD", action="long", entry_price=1, window_days=30,
        )
        db_session.add(outcome)
        await db_session.flush()

        entry_stored = (
            await db_session.execute(
                select(JournalEntry.timestamp, JournalEntry.created_at)
                .where(JournalEntry.id == entry.id)
            )
        ).one()
        outcome_checked = (
            await db_session.execute(
                select(JournalOutcome.checked_at).where(JournalOutcome.id == outcome.id)
            )
        ).scalar_one()

        assert entry_stored[0] == AN_INSTANT
        assert entry_stored[1].tzinfo is not None
        assert outcome_checked.tzinfo is not None

    async def test_a_broker_order(self, db_session):
        from app.models.broker_order import BrokerOrder

        card, user_id = await self._a_card(db_session)
        order = BrokerOrder(
            id=uuid.uuid4(), user_id=user_id, symbol="EURUSD",
            action="long", size=1, status="pending", mode="paper",
            confirmed_at=AN_INSTANT,
        )
        db_session.add(order)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(BrokerOrder.confirmed_at, BrokerOrder.created_at, BrokerOrder.updated_at)
                .where(BrokerOrder.id == order.id)
            )
        ).one()

        assert stored[0] == AN_INSTANT
        assert stored[1].tzinfo is not None and stored[2].tzinfo is not None
