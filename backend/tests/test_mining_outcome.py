"""
What a mining run reports about itself — #86.

The run's row says how many strategy cards it created and updated, which
clusters failed and why, and — when a failure would have repeated for every
cluster left, like a rejected API key — that it stopped, said once rather than
once per cluster. A run whose 22 calls all failed with `401 invalid x-api-key`
used to log 22 errors and look, in the Library, like a run that found nothing.
"""
import json

import anthropic
import httpx
import pytest

from app.services import pattern_mining_service
from tests.conftest import make_card, make_source, make_workspace
from tests.test_mining_truncation import COMPLETE, _FakeResponse, _three_ideas

pytestmark = pytest.mark.db


def _rejected_key():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request, json={"error": {"type": "authentication_error"}})
    return anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)


@pytest.fixture
def claude(monkeypatch):
    """Answer every card generation call with `reply`, counting the calls."""
    monkeypatch.setattr(pattern_mining_service.settings, "ANTHROPIC_API_KEY", "test-key")

    async def _not_billed(**kwargs):
        pass

    monkeypatch.setattr(pattern_mining_service, "log_usage_event", _not_billed)
    calls = []

    def _install(reply):
        class _FakeMessages:
            async def create(self, **kwargs):
                calls.append(kwargs)
                if isinstance(reply, Exception):
                    raise reply
                return _FakeResponse(reply, "end_turn")

        class _FakeClient:
            def __init__(self, **kwargs):
                self.messages = _FakeMessages()

        monkeypatch.setattr(pattern_mining_service.anthropic, "AsyncAnthropic", _FakeClient)
        return calls

    return _install


class TestWhatTheRunDid:
    async def test_counts_cards_created_and_cards_updated_apart(self, db_session, claude):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        existing = await make_card(db_session, workspace)  # EURUSD/breakout
        await _three_ideas(db_session, source, symbol="EURUSD", setup_type="breakout")
        await _three_ideas(db_session, source, symbol="GBPUSD", setup_type="pullback")
        claude(json.dumps(COMPLETE))

        outcome = await pattern_mining_service.run_pattern_mining(db_session, workspace.id)

        assert (outcome.cards_created, outcome.cards_updated) == (1, 1)
        assert outcome.failures == []
        assert outcome.stopped_because is None
        await db_session.refresh(existing)
        assert existing.version == 2


class TestAFailureThatWouldRepeat:
    async def test_a_rejected_api_key_stops_the_run_and_is_said_once(self, db_session, claude):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        for symbol in ("EURUSD", "GBPUSD", "AUDUSD"):
            await _three_ideas(db_session, source, symbol=symbol, setup_type="breakout")
        calls = claude(_rejected_key())

        outcome = await pattern_mining_service.run_pattern_mining(db_session, workspace.id)

        assert len(calls) == 1
        assert outcome.failures == []
        assert "API key" in outcome.stopped_because
        assert "ANTHROPIC_API_KEY" in outcome.stopped_because

    async def test_a_missing_api_key_stops_the_run_before_any_call(self, db_session, claude, monkeypatch):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        await _three_ideas(db_session, source)
        calls = claude(json.dumps(COMPLETE))
        monkeypatch.setattr(pattern_mining_service.settings, "ANTHROPIC_API_KEY", "")

        outcome = await pattern_mining_service.run_pattern_mining(db_session, workspace.id)

        assert calls == []
        assert "ANTHROPIC_API_KEY" in outcome.stopped_because

    async def test_cards_written_before_it_stopped_are_kept_and_counted(self, db_session, claude, monkeypatch):
        workspace = await make_workspace(db_session)
        source = await make_source(db_session, workspace)
        # Three ideas each, so the clusters are equal: their order is then the
        # query's own, and which one fails is decided by the fake below.
        await _three_ideas(db_session, source, symbol="EURUSD", setup_type="breakout")
        await _three_ideas(db_session, source, symbol="GBPUSD", setup_type="breakout")
        answers = [json.dumps(COMPLETE), _rejected_key()]

        class _FakeMessages:
            async def create(self, **kwargs):
                answer = answers.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return _FakeResponse(answer, "end_turn")

        class _FakeClient:
            def __init__(self, **kwargs):
                self.messages = _FakeMessages()

        claude(json.dumps(COMPLETE))
        monkeypatch.setattr(pattern_mining_service.anthropic, "AsyncAnthropic", _FakeClient)

        outcome = await pattern_mining_service.run_pattern_mining(db_session, workspace.id)

        assert outcome.cards_created == 1
        assert outcome.stopped_because is not None
