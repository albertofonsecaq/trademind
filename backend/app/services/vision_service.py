"""
Claude Vision service for image and video-frame analysis.

Single call combines:
  1. Trading-relevance judgment (replaces the Haiku relevance filter for media)
  2. Content extraction (OCR, chart pattern, drawn levels, annotations)

Per spec: "the vision/transcription pass itself doubles as the relevance judgment …
so cost is only spent once, not as a separate redundant check."
"""
from __future__ import annotations
import base64
import json
import logging
import io
import anthropic
from decimal import Decimal
from app.core.config import settings

log = logging.getLogger(__name__)

# Vision uses Sonnet (same model as distillation) — it handles charts well
_VISION_MODEL = settings.DISTILLATION_MODEL

_SYSTEM = """You are a trading chart and financial image analyst.

Analyze the provided image and return ONLY valid JSON with this schema:
{
  "is_on_topic": true | false,
  "reason": "one sentence — why this image is or isn't trading-related",
  "description": "detailed description of trading content, or null if off-topic",
  "confidence": 0.0–1.0,
  "ocr_text": "any visible text, ticker symbols, price levels, date labels — verbatim, or null"
}

is_on_topic = true for: price charts, candlestick/bar/line charts, order books, trading setups
  with drawn levels/annotations, technical indicators, economic data tables, stock screener
  output, brokerage screenshots, company earnings charts, options chains.
is_on_topic = false for: unrelated memes, food/travel photos, personal photos, sports,
  news with no market content, generic infographics unrelated to financial markets.

When is_on_topic = true, description should include:
- Instrument / ticker if visible
- Chart type and timeframe if visible
- Key price levels, patterns, drawn lines/zones
- Indicator readings (RSI, MACD, moving averages, etc.) if visible
- Any annotations, arrows, highlighted areas"""


async def extract_image(
    image_bytes: bytes,
    media_type: str = "image/jpeg",
) -> tuple[dict, dict]:
    """
    Analyze an image with Claude Vision.
    Returns (result_dict, token_counts).
    result_dict keys: is_on_topic, reason, description, confidence, ocr_text
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    # Resize large images to cap token cost (Vision charges per pixel)
    image_bytes = _maybe_resize(image_bytes, max_pixels=1_500_000)

    b64 = base64.standard_b64encode(image_bytes).decode()
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    response = await client.messages.create(
        model=_VISION_MODEL,
        max_tokens=512,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": "Analyze this image."},
                ],
            }
        ],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Vision response not valid JSON: %s…", raw[:120])
        result = {
            "is_on_topic": False,
            "reason": "Could not parse vision response",
            "description": None,
            "confidence": 0.0,
            "ocr_text": None,
        }

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return result, usage


def _maybe_resize(image_bytes: bytes, max_pixels: int = 1_500_000) -> bytes:
    """Resize image if it exceeds max_pixels to keep vision token cost predictable."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w * h <= max_pixels:
            return image_bytes
        ratio = (max_pixels / (w * h)) ** 0.5
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return image_bytes  # return original if PIL fails


def vision_cost(input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate cost using Sonnet pricing."""
    return Decimal(str(0.000003 * input_tokens + 0.000015 * output_tokens))
