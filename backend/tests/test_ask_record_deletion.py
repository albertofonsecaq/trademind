"""
What a deleted ask record takes with it — #74, against a real database.

Pruning a history has to remove the history and nothing else, and the things
around an ask record are exactly the things that must not go with it:

- the **usage event** for the call that produced the answer. The money was
  spent and is still owed, whatever the user later thinks of the question. The
  budget cap, the overage metering and the margin figures are all derived from
  those rows, so one that disappeared would be spend that never reached the cap
  — #27's shape, arrived at from the other direction.
- the **evidence** the answer cited and the **trade ideas** distilled from it.
  The knowledge base is the workspace's, not one user's transcript, and a
  citation is not ownership.
- every **strategy card**, for the same reason.

A foreign key, a cascade or a relationship added later could quietly make any
of that untrue, and no fake would notice: what deletes a row here is Postgres,
not the mapping. So this file uses a real database, counts what is around the
record before and after, and is the test the ticket asks for by name.

Needs a database, so it is skipped unless one is configured. See conftest.py.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.api import ask as ask_api
from app.models.ask_record import AskRecord
from app.models.evidence_item import EvidenceItem
from app.models.strategy_card import StrategyCard
from app.models.trade_idea import TradeIdea
from app.models.usage_event import UsageEvent
from app.models.workspace_member import WorkspaceMember
from tests.conftest import make_card, make_source, make_user, make_workspace

pytestmark = pytest.mark.db


async def _asker(session):
    user = await make_user(session)
    workspace = await make_workspace(session, user=user)
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="owner",
    ))
    await session.flush()
    return user, workspace


async def _ask(session, user, workspace, question="what happened to EURUSD?"):
    record = AskRecord(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id,
        question=question, answer="it broke out twice [1]",
        sources=[{"index": 1, "stable_id": "telegram:chan:1"}], language="en",
    )
    session.add(record)
    await session.flush()
    return record


async def _the_things_around_it(session, workspace):
    """A usage event, a piece of evidence, an idea from it, and a card."""
    source = await make_source(session, workspace=workspace)
    session.add(UsageEvent(
        id=uuid.uuid4(), workspace_id=workspace.id, task_type="query_synthesis",
        provider_model="claude-sonnet-5", input_units=900, output_units=120,
        cost_usd=Decimal("0.0045"),
    ))
    evidence = EvidenceItem(
        id=uuid.uuid4(), workspace_id=workspace.id, source_config_id=source.id,
        stable_id="telegram:chan:1", source_type="telegram", type="text",
        content="EURUSD looks like a breakout", is_on_topic=True,
    )
    session.add(evidence)
    await session.flush()
    session.add(TradeIdea(
        id=uuid.uuid4(), evidence_item_id=evidence.id, workspace_id=workspace.id,
        setup_type="breakout", symbol="EURUSD", action="long",
    ))
    await make_card(session, workspace=workspace)
    await session.flush()


async def _counts(session, workspace):
    around = {}
    for model in (UsageEvent, EvidenceItem, TradeIdea, StrategyCard):
        total = await session.execute(
            select(func.count()).select_from(model).where(model.workspace_id == workspace.id)
        )
        around[model.__name__] = total.scalar()
    return around


async def _records_of(session, user, workspace):
    found = await session.execute(
        select(AskRecord.question)
        .where(AskRecord.user_id == user.id, AskRecord.workspace_id == workspace.id)
        .order_by(AskRecord.created_at)
    )
    return list(found.scalars().all())


class TestDeletingOneLeavesEverythingElse:
    async def test_the_record_goes_and_nothing_around_it_does(self, db_session):
        user, workspace = await _asker(db_session)
        await _the_things_around_it(db_session, workspace)
        record = await _ask(db_session, user, workspace)
        before = await _counts(db_session, workspace)

        await ask_api.delete_ask_record(
            workspace_id=workspace.id, record_id=record.id,
            current_user=user, db=db_session,
        )

        assert await _records_of(db_session, user, workspace) == []
        assert await _counts(db_session, workspace) == before

    async def test_deleting_the_same_record_twice_is_not_an_error(self, db_session):
        """What a double click is. The second one matches nothing and says so."""
        user, workspace = await _asker(db_session)
        record = await _ask(db_session, user, workspace)

        for _ in range(2):
            await ask_api.delete_ask_record(
                workspace_id=workspace.id, record_id=record.id,
                current_user=user, db=db_session,
            )

        assert await _records_of(db_session, user, workspace) == []

    async def test_another_members_record_is_not_deleted_and_not_reported(self, db_session):
        """
        Both are members of the workspace. The delete matches nothing, returns
        the same way a real one does, and the record is still there — the one
        assertion a fake cannot make.
        """
        mine, workspace = await _asker(db_session)
        theirs = await make_user(db_session)
        db_session.add(WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=theirs.id, role="member",
        ))
        await db_session.flush()
        not_mine = await _ask(db_session, theirs, workspace, question="theirs")

        await ask_api.delete_ask_record(
            workspace_id=workspace.id, record_id=not_mine.id,
            current_user=mine, db=db_session,
        )

        assert await _records_of(db_session, theirs, workspace) == ["theirs"]


class TestClearingTakesOnlyTheCallersOwn:
    async def test_it_clears_mine_and_leaves_theirs(self, db_session):
        mine, workspace = await _asker(db_session)
        theirs = await make_user(db_session)
        db_session.add(WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=theirs.id, role="member",
        ))
        await db_session.flush()
        for question in ("mine one", "mine two"):
            await _ask(db_session, mine, workspace, question=question)
        await _ask(db_session, theirs, workspace, question="theirs")

        cleared = await ask_api.clear_ask_records(
            workspace_id=workspace.id, current_user=mine, db=db_session
        )

        assert cleared.deleted == 2
        assert await _records_of(db_session, mine, workspace) == []
        assert await _records_of(db_session, theirs, workspace) == ["theirs"]

    async def test_it_leaves_the_same_users_records_in_another_workspace(self, db_session):
        """
        One user, two workspaces. Clearing is scoped to the one they are in, so
        a history cleared in one is not a history cleared everywhere.
        """
        user, here = await _asker(db_session)
        elsewhere = await make_workspace(db_session, user=user)
        db_session.add(WorkspaceMember(
            id=uuid.uuid4(), workspace_id=elsewhere.id, user_id=user.id, role="owner",
        ))
        await db_session.flush()
        await _ask(db_session, user, here, question="asked here")
        await _ask(db_session, user, elsewhere, question="asked elsewhere")

        cleared = await ask_api.clear_ask_records(
            workspace_id=here.id, current_user=user, db=db_session
        )

        assert cleared.deleted == 1
        assert await _records_of(db_session, user, here) == []
        assert await _records_of(db_session, user, elsewhere) == ["asked elsewhere"]

    async def test_clearing_an_empty_history_takes_nothing(self, db_session):
        user, workspace = await _asker(db_session)

        cleared = await ask_api.clear_ask_records(
            workspace_id=workspace.id, current_user=user, db=db_session
        )

        assert cleared.deleted == 0


class TestCountingWhatWouldGo:
    async def test_it_counts_the_callers_own_in_this_workspace_only(self, db_session):
        mine, workspace = await _asker(db_session)
        theirs = await make_user(db_session)
        db_session.add(WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=theirs.id, role="member",
        ))
        await db_session.flush()
        for question in ("one", "two", "three"):
            await _ask(db_session, mine, workspace, question=question)
        await _ask(db_session, theirs, workspace, question="theirs")

        counted = await ask_api.count_ask_records(
            workspace_id=workspace.id, current_user=mine, db=db_session
        )

        assert counted.count == 3
