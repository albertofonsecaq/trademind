"""
Regression tests for #83 — a cut-off mining reply was saved as a strategy card.

max_tokens sat at 600, about what a bilingual description and a four-step
flowchart need, so Claude's reply stopped mid-JSON. The parse failure then wrote
"A recurring <setup> pattern observed on <symbol>." and an empty flowchart as
the card, or as a new version of an existing one: in a real run, every cluster.

A reply that cannot be used is now a failed generation for that cluster, which
mining already skips and logs. Nothing in a card is ever a placeholder.
"""
import json
import uuid

import pytest
from sqlalchemy import select

from app.models.evidence_item import EvidenceItem
from app.models.strategy_card import StrategyCard
from app.models.trade_idea import TradeIdea
from app.services import pattern_mining_service
from app.services.pattern_mining_service import CardGenerationFailed, _generate_card_content
from tests.conftest import make_card, make_source, make_workspace

COMPLETE = {
    "description_en": "BTCUSD breaks above a range high on volume.",
    "description_es": "BTCUSD rompe por encima de un máximo de rango con volumen.",
    "flowchart_spec": {
        "entry": "Daily close above the range high",
        "confirmation": "Retest of the range high holds",
        "risk_management": "Stop below the retest low",
        "exit": "Measured move of the range height",
    },
}

# A real reply, cut where max_tokens ran out.
TRUNCATED = (
    '```json\n{\n  "description_en": "BTCUSD is exhibiting a breakout setup across multiple '
    'timeframes, with price clearing key range highs and ascending triangle resistance. The pat'
)

SAMPLES = [{"action": "long", "entry": 67000, "target": 70000, "stop": 65500,
            "summary_en": "BTC breaking above range high on volume"}]


class _FakeResponse:
    def __init__(self, text, stop_reason):
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"input_tokens": 900, "output_tokens": 600})()


@pytest.fixture
def reply(monkeypatch):
    """Make the card generation call return a chosen raw reply, with no network or API key."""
    monkeypatch.setattr(pattern_mining_service.settings, "ANTHROPIC_API_KEY", "test-key")

    def _install(text, stop_reason="end_turn"):
        class _FakeMessages:
            async def create(self, **kwargs):
                return _FakeResponse(text, stop_reason)

        class _FakeClient:
            def __init__(self, **kwargs):
                self.messages = _FakeMessages()

        monkeypatch.setattr(pattern_mining_service.anthropic, "AsyncAnthropic", _FakeClient)

    return _install


async def generate():
    return await _generate_card_content("BTCUSD", "breakout", SAMPLES)


class TestAReplyThatCannotBeUsed:
    async def test_a_cut_off_reply_is_a_failure_not_a_card(self, reply):
        reply(TRUNCATED, "max_tokens")

        with pytest.raises(CardGenerationFailed, match="cut off"):
            await generate()

    async def test_a_capped_reply_is_a_failure_even_if_it_parses(self, reply):
        """stop_reason=max_tokens means the content is incomplete, whatever it parses to."""
        reply(json.dumps(COMPLETE), "max_tokens")

        with pytest.raises(CardGenerationFailed, match="cut off"):
            await generate()

    async def test_a_reply_that_is_not_json_is_a_failure(self, reply):
        reply("Here is the strategy card you asked for: a breakout.")

        with pytest.raises(CardGenerationFailed, match="not valid JSON"):
            await generate()

    @pytest.mark.parametrize("missing", ["description_en", "description_es", "flowchart_spec"])
    async def test_a_reply_missing_a_part_of_the_card_is_a_failure(self, reply, missing):
        reply(json.dumps({k: v for k, v in COMPLETE.items() if k != missing}))

        with pytest.raises(CardGenerationFailed, match=missing):
            await generate()

    async def test_a_reply_with_an_empty_flowchart_step_is_a_failure(self, reply):
        card = {**COMPLETE, "flowchart_spec": {**COMPLETE["flowchart_spec"], "exit": ""}}
        reply(json.dumps(card))

        with pytest.raises(CardGenerationFailed, match="exit"):
            await generate()


class TestHeadroom:
    def test_max_tokens_clears_the_observed_reply_length(self):
        """A three-idea cluster answered this prompt in 576 tokens; 600 cut it off."""
        assert pattern_mining_service._MAX_TOKENS >= 1500


class TestAUsableReply:
    async def test_is_the_card_as_written(self, reply):
        reply("```json\n" + json.dumps(COMPLETE) + "\n```")

        fields, usage = await generate()

        assert fields == COMPLETE
        assert usage == {"input_tokens": 900, "output_tokens": 600}


class TestAFailedReplyIsStillPaidFor:
    async def test_a_reply_with_no_text_is_a_failure_that_carries_its_cost(self, reply, monkeypatch):
        reply("")
        original = _FakeResponse.__init__

        def _no_blocks(self, text, stop_reason):
            original(self, text, stop_reason)
            self.content = []

        monkeypatch.setattr(_FakeResponse, "__init__", _no_blocks)

        with pytest.raises(CardGenerationFailed, match="no text") as failed:
            await generate()

        assert failed.value.usage == {"input_tokens": 900, "output_tokens": 600}

    async def test_the_failure_carries_what_the_call_cost(self, reply):
        """The reply was generated and billed whether or not it became a card."""
        reply(TRUNCATED, "max_tokens")

        with pytest.raises(CardGenerationFailed) as failed:
            await generate()

        assert failed.value.usage == {"input_tokens": 900, "output_tokens": 600}


async def _three_ideas(session, source, symbol="EURUSD", setup_type="breakout"):
    """A cluster mining will pick up: three long ideas on one symbol and setup."""
    for _ in range(3):
        evidence = EvidenceItem(
            id=uuid.uuid4(), workspace_id=source.workspace_id, source_config_id=source.id,
            stable_id=f"telegram:chan:{uuid.uuid4().hex[:6]}", source_type="telegram",
            type="text", content=f"{symbol} {setup_type}", confidence=1.0,
        )
        session.add(evidence)
        await session.flush()
        session.add(TradeIdea(
            id=uuid.uuid4(), evidence_item_id=evidence.id, workspace_id=source.workspace_id,
            symbol=symbol, setup_type=setup_type, action="long",
        ))
        await session.flush()


@pytest.mark.db
class TestMiningWithAReplyThatCannotBeUsed:
    @pytest.fixture
    def billed(self, monkeypatch):
        calls = []

        async def _record(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(pattern_mining_service, "log_usage_event", _record)
        return calls

    async def test_no_card_is_created(self, db_session, reply, billed):
        source = await make_source(db_session)
        await _three_ideas(db_session, source)
        reply(TRUNCATED, "max_tokens")

        touched = await pattern_mining_service.run_pattern_mining(db_session, source.workspace_id)

        cards = (await db_session.execute(
            select(StrategyCard).where(StrategyCard.workspace_id == source.workspace_id)
        )).scalars().all()
        assert touched == 0
        assert cards == []

    async def test_an_existing_card_is_left_as_it_was(self, db_session, reply, billed):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        card = await make_card(db_session, workspace)
        await _three_ideas(db_session, source, symbol=card.symbol_scope, setup_type=card.setup_type)
        reply("Not a card at all.")

        await pattern_mining_service.run_pattern_mining(db_session, source.workspace_id)
        await db_session.refresh(card)

        assert card.version == 1
        assert card.version_history == []
        assert card.description_en == "a breakout"

    async def test_the_failed_reply_is_still_billed(self, db_session, reply, billed):
        source = await make_source(db_session)
        await _three_ideas(db_session, source)
        reply(TRUNCATED, "max_tokens")

        await pattern_mining_service.run_pattern_mining(db_session, source.workspace_id)

        [call] = billed
        assert (call["input_units"], call["output_units"]) == (900, 600)
