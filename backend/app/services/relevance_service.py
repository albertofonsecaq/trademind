"""
Relevance filter — runs BEFORE any expensive processing.
Uses Claude Haiku (cheapest model) to classify content against workspace topic_scope.
Off-topic content is tagged and skipped from all downstream steps.
"""
from __future__ import annotations
import anthropic
from app.core.config import settings

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


_SYSTEM = (
    "You are a content classifier for a trading knowledge base. "
    "Decide if a piece of text is relevant to the given topic scope. "
    "Reply with exactly two lines:\n"
    "Line 1: yes or no\n"
    "Line 2: one sentence explaining why (max 20 words)"
)


async def is_on_topic(text: str, topic_scope: str) -> tuple[bool, str, dict]:
    """
    Returns (on_topic, reason, token_counts).
    token_counts has keys: input_tokens, output_tokens.
    """
    client = _get_client()
    response = await client.messages.create(
        model=settings.RELEVANCE_MODEL,
        max_tokens=80,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": f"Topic scope: {topic_scope}\n\nContent:\n{text[:1500]}",
            }
        ],
    )
    raw = response.content[0].text.strip()
    lines = raw.split("\n", 1)
    verdict = lines[0].strip().lower().startswith("yes")
    reason = lines[1].strip() if len(lines) > 1 else raw

    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return verdict, reason, usage
