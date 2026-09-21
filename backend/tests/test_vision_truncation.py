"""
Regression tests for #3 — a truncated vision reply was recorded as a verdict.

max_tokens sat below the reply length the system prompt asks for, so the JSON
arrived unterminated. The parse failure then wrote is_on_topic=False, which is
indistinguishable from a genuine off-topic rejection: 53% of ingested images
were analyzed, paid for, and silently discarded.
"""
import json

import pytest

from app.services import vision_service

# A real reply, cut where max_tokens ran out.
TRUNCATED = (
    '```json\n{\n  "is_on_topic": true,\n'
    '  "reason": "Candlestick chart of BTCUSD on Binance with drawn levels",\n'
    '  "description": "4H chart showing a bullish flag breakout with resistance at 46,8'
)
COMPLETE = {
    "is_on_topic": True,
    "reason": "Candlestick chart of BTCUSD",
    "description": "4H chart, bullish flag breakout",
    "confidence": 0.9,
    "ocr_text": "BTCUSD 4H",
}


class _FakeMessages:
    def __init__(self, text, stop_reason):
        self._text = text
        self._stop_reason = stop_reason

    async def create(self, **kwargs):
        return _FakeResponse(self._text, self._stop_reason)


class _FakeResponse:
    def __init__(self, text, stop_reason):
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"input_tokens": 1200, "output_tokens": 512})()


@pytest.fixture
def reply(monkeypatch):
    """Make extract_image return a chosen raw reply, with no network or API key."""
    monkeypatch.setattr(vision_service.settings, "ANTHROPIC_API_KEY", "test-key")

    def _install(text, stop_reason):
        class _FakeClient:
            def __init__(self, **kwargs):
                self.messages = _FakeMessages(text, stop_reason)

        monkeypatch.setattr(vision_service.anthropic, "AsyncAnthropic", _FakeClient)

    return _install


IMAGE = b"not-a-real-jpeg"   # _maybe_resize returns the input untouched if PIL can't read it


class TestTruncatedReply:
    async def test_is_not_recorded_as_a_verdict(self, reply):
        """The bug in one assertion: a cut-off reply must never read as 'off-topic'."""
        reply(TRUNCATED, "max_tokens")
        result, _ = await vision_service.extract_image(IMAGE)

        assert result["is_on_topic"] is not False
        assert result["is_on_topic"] is None          # unknown, not rejected
        assert result["needs_reprocessing"] is True

    async def test_reason_names_truncation(self, reply):
        reply(TRUNCATED, "max_tokens")
        result, _ = await vision_service.extract_image(IMAGE)
        assert "truncated" in result["reason"].lower()

    async def test_parsed_but_capped_reply_is_still_partial(self, reply):
        """stop_reason=max_tokens means the content is incomplete even if it parses."""
        reply(json.dumps(COMPLETE), "max_tokens")
        result, _ = await vision_service.extract_image(IMAGE)
        assert result["needs_reprocessing"] is True


class TestUnparseableReply:
    async def test_is_flagged_rather_than_rejected(self, reply):
        reply("I can't help with that.", "end_turn")
        result, _ = await vision_service.extract_image(IMAGE)

        assert result["is_on_topic"] is None
        assert result["needs_reprocessing"] is True
        assert result["reason"] == "Could not parse vision response"


class TestCleanReply:
    async def test_is_returned_untouched(self, reply):
        reply(json.dumps(COMPLETE), "end_turn")
        result, usage = await vision_service.extract_image(IMAGE)

        assert result["is_on_topic"] is True
        assert result["needs_reprocessing"] is False
        assert result["description"] == "4H chart, bullish flag breakout"
        assert usage == {"input_tokens": 1200, "output_tokens": 512}

    async def test_fenced_json_is_unwrapped(self, reply):
        reply(f"```json\n{json.dumps(COMPLETE)}\n```", "end_turn")
        result, _ = await vision_service.extract_image(IMAGE)
        assert result["is_on_topic"] is True
        assert result["needs_reprocessing"] is False


class TestHeadroom:
    def test_max_tokens_clears_the_observed_reply_length(self):
        """Annotated charts answered this prompt in ~600 tokens; 512 cut them off."""
        assert vision_service._MAX_TOKENS >= 1024
