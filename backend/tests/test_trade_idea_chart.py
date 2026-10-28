"""
A trade idea drawn against what price did around it — #109.

Reading that someone proposed a long on EURUSD at 1.0850 says little until it is
seen against the candles of the days around it. The chart is the idea's symbol
in daily bars, from a month before it was proposed to the end of the window its
outcome check looks at, with its entry, target and stop.

Offline: the market data is answered locally, nothing reaches a provider.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import strategy_cards as cards_api
from app.api import trade_ideas as trade_ideas_api
from app.connectors.market_data import PriceSource
from app.core.config import settings
from app.models.evidence_item import EvidenceItem
from app.models.outcome_check import OutcomeCheck
from app.models.trade_idea import TradeIdea
from app.models.workspace_member import WorkspaceMember
from app.services import trade_idea_chart
from tests.conftest import make_card, make_source, make_user, make_workspace

PROPOSED = datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc)


def _idea(**overrides):
    fields = dict(
        symbol="EURUSD", action="long", message_timestamp=PROPOSED,
        entry=Decimal("1.0850"), target=Decimal("1.0950"), stop=Decimal("1.0800"),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _bar(day: date, close: float = 1.09) -> dict:
    return {"date": day, "open": close - 0.002, "high": close + 0.004, "low": close - 0.005, "close": close, "volume": 0.0}


class FakeMarket:
    """Answers get_bars with what a test registered, and records what it was asked."""

    def __init__(self, bars: list[dict] | None = None):
        self.bars = bars or []
        self.asked: list[tuple] = []

    async def get_bars(self, symbol, start, days=None):
        self.asked.append((symbol, start, days))
        return self.bars

    def price_source(self, symbol):
        return PriceSource(priced_as=symbol, stands_in_for_spot=False)


class TestAnIdeasChart:
    async def test_covers_a_month_before_it_to_the_end_of_its_outcome_window(self):
        market = FakeMarket()

        await trade_idea_chart.chart_of(_idea(), market)

        assert market.asked == [
            ("EURUSD", date(2026, 8, 16), trade_idea_chart.DAYS_BEFORE + settings.VALIDATION_WINDOW_DAYS)
        ]

    async def test_carries_the_candles_and_the_levels_it_was_proposed_at(self):
        market = FakeMarket([_bar(date(2026, 9, 15), 1.0860), _bar(date(2026, 9, 16), 1.0900)])

        chart = await trade_idea_chart.chart_of(_idea(), market)

        assert (chart.symbol, chart.action, chart.proposed_on) == ("EURUSD", "long", date(2026, 9, 15))
        assert (chart.entry, chart.target, chart.stop) == (Decimal("1.0850"), Decimal("1.0950"), Decimal("1.0800"))
        assert [(c.day, c.close) for c in chart.candles] == [(date(2026, 9, 15), 1.0860), (date(2026, 9, 16), 1.0900)]
        first = chart.candles[0]
        assert (first.open, first.high, first.low) == pytest.approx((1.0840, 1.0900, 1.0810))


class TestNoChart:
    @pytest.mark.parametrize("why", [
        {"symbol": None},
        {"action": "info"},
        {"action": "commentary"},
        {"action": None},
        {"message_timestamp": None},
    ], ids=["no symbol", "information", "commentary", "no direction", "no date to place it on"])
    async def test_for_an_idea_that_is_not_a_trade_on_a_day(self, why):
        market = FakeMarket([_bar(date(2026, 9, 15))])

        assert await trade_idea_chart.chart_of(_idea(**why), market) is None
        assert market.asked == []           # no provider call for a chart nobody gets

    async def test_is_not_an_error_when_the_provider_has_no_prices(self):
        # An unknown symbol, a range Yahoo has nothing for yet, or Yahoo down:
        # the connector already logs which, and answers with no bars.
        chart = await trade_idea_chart.chart_of(_idea(), FakeMarket([]))

        assert chart.candles == []


# ── through the API ──────────────────────────────────────────────────────────


async def _member(session, workspace):
    user = await make_user(session)
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="member"))
    await session.flush()
    return user


async def _stored_idea(session, workspace, **overrides):
    source = await make_source(session, workspace)
    item = EvidenceItem(
        id=uuid.uuid4(), workspace_id=workspace.id, source_config_id=source.id,
        stable_id=f"telegram:chan:{uuid.uuid4().hex[:8]}", source_type="telegram",
        type="text", content="EURUSD long 1.0850, target 1.0950, stop 1.0800",
    )
    session.add(item)
    await session.flush()
    fields = dict(
        id=uuid.uuid4(), evidence_item_id=item.id, workspace_id=workspace.id,
        symbol="EURUSD", action="long", message_timestamp=PROPOSED,
        entry=Decimal("1.0850"), target=Decimal("1.0950"), stop=Decimal("1.0800"),
    )
    fields.update(overrides)
    idea = TradeIdea(**fields)
    session.add(idea)
    await session.flush()
    return idea


@pytest.mark.db
class TestTheChartEndpoint:
    async def test_a_member_gets_the_ideas_chart(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        idea = await _stored_idea(db_session, workspace)
        market = FakeMarket([_bar(date(2026, 9, 15), 1.0860)])

        chart = await trade_ideas_api.get_trade_idea_chart(workspace.id, idea.id, user, db_session, market)

        assert (chart.symbol, chart.entry, chart.target, chart.stop) == ("EURUSD", 1.085, 1.095, 1.08)
        assert [(c.time, c.close) for c in chart.candles] == [("2026-09-15", 1.0860)]

    async def test_carries_how_the_ideas_outcome_check_resolved_it(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        idea = await _stored_idea(db_session, workspace)
        db_session.add(OutcomeCheck(
            trade_idea_id=idea.id, workspace_id=workspace.id, symbol="EURUSD", action="long",
            trade_date=date(2026, 9, 15), outcome="won", outcome_date=date(2026, 9, 22),
            window_days=20, max_adverse_excursion=Decimal("0.0030"), max_favorable_excursion=Decimal("0.0100"),
        ))
        await db_session.flush()

        chart = await trade_ideas_api.get_trade_idea_chart(workspace.id, idea.id, user, db_session, FakeMarket())

        assert chart.outcome.outcome == "won"
        assert chart.outcome.outcome_date == "2026-09-22"
        assert (chart.outcome.window_start, chart.outcome.window_end) == ("2026-09-15", "2026-10-05")
        assert (chart.outcome.worst_price, chart.outcome.best_price) == (1.082, 1.095)
        assert chart.no_outcome_because is None

    async def test_says_when_the_idea_was_never_checked(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        idea = await _stored_idea(db_session, workspace)

        chart = await trade_ideas_api.get_trade_idea_chart(workspace.id, idea.id, user, db_session, FakeMarket())

        assert chart.outcome is None
        assert chart.no_outcome_because == "not_checked"

    async def test_an_idea_that_cannot_be_charted_is_not_found(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        idea = await _stored_idea(db_session, workspace, action="commentary")

        with pytest.raises(HTTPException) as refused:
            await trade_ideas_api.get_trade_idea_chart(workspace.id, idea.id, user, db_session, FakeMarket())

        assert refused.value.status_code == 404

    async def test_another_workspaces_idea_is_not_found_through_your_own(self, db_session):
        mine, theirs = await make_workspace(db_session), await make_workspace(db_session)
        user = await _member(db_session, mine)
        their_idea = await _stored_idea(db_session, theirs)
        market = FakeMarket()

        with pytest.raises(HTTPException) as refused:
            await trade_ideas_api.get_trade_idea_chart(mine.id, their_idea.id, user, db_session, market)

        assert refused.value.status_code == 404
        assert market.asked == []

    async def test_someone_outside_the_workspace_is_refused(self, db_session):
        workspace = await make_workspace(db_session)
        outsider = await make_user(db_session)
        idea = await _stored_idea(db_session, workspace)

        with pytest.raises(HTTPException) as refused:
            await trade_ideas_api.get_trade_idea_chart(workspace.id, idea.id, outsider, db_session, FakeMarket())

        assert refused.value.status_code == 403


@pytest.mark.db
class TestAStrategyCardsExamples:
    async def test_say_which_can_be_opened_as_a_chart(self, db_session):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        trade = await _stored_idea(db_session, workspace)
        remark = await _stored_idea(db_session, workspace, action="commentary")
        card = await make_card(db_session, workspace)
        card.supporting_evidence = [str(trade.id), str(remark.id)]
        await db_session.flush()

        detail = await cards_api.get_strategy_card_detail(workspace.id, card.id, 10, user, db_session)

        shown = {example.id: example for example in detail.examples}
        assert shown[trade.id].has_chart is True
        assert shown[remark.id].has_chart is False
