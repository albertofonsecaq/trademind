"""
Distillation service — only called on evidence tagged is_on_topic=True.
Extracts structured trade_idea and generates bilingual summaries (EN + ES).
"""
from __future__ import annotations
import json
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


_SYSTEM = """You are a trading signal extractor for a personal knowledge base.

Extract structured information from trading content and return ONLY valid JSON.

Output schema (all fields optional except summary_en and summary_es):
{
  "symbol": "TICKER or null",
  "setup_type": "e.g. breakout, pullback, wedge, earnings play, or null",
  "action": "long | short | info | commentary",
  "entry": number or null,
  "target": number or null,
  "stop": number or null,
  "summary_en": "1-2 sentence plain-language summary in English",
  "summary_es": "1-2 sentence plain-language summary in Spanish"
}

Rules:
- action=info for general market commentary without a clear directional call
- action=commentary for opinion/analysis with no specific trade setup
- Normalize symbols to uppercase without $: NVDA not $NVDA
- If entry/target/stop are percentages or relative levels, set them to null
- Always produce both summary_en and summary_es regardless of source language"""


async def distill(text: str, author: str | None, channel: str | None) -> tuple[dict, dict]:
    """
    Returns (extracted_fields, token_counts).
    extracted_fields matches the JSON schema above.
    """
    client = _get_client()
    response = await client.messages.create(
        model=settings.DISTILLATION_MODEL,
        max_tokens=400,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Channel: {channel or 'unknown'}\n"
                    f"Author: {author or 'unknown'}\n\n"
                    f"Content:\n{text[:3000]}"
                ),
            }
        ],
    )
    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        fields = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: return minimal structure with raw as commentary
        fields = {
            "symbol": None, "setup_type": None, "action": "commentary",
            "entry": None, "target": None, "stop": None,
            "summary_en": text[:200], "summary_es": text[:200],
        }

    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return fields, usage
