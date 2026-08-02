"""
Query synthesis: takes retrieved evidence and generates a cited answer.
Called from the Ask endpoint. Always logs a usage_event.
Budget/overage check is done by the caller (api/ask.py).
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


_SYSTEM_EN = """You are TradeMind, a trading knowledge assistant.
Answer the user's question using ONLY the provided evidence items.
For every factual claim, cite the source number like [1] or [2].
If the evidence does not contain enough information to answer, say so clearly.
Do not give financial advice or make future price predictions.
Summarize the historical patterns and ideas found in the evidence."""

_SYSTEM_ES = """Eres TradeMind, un asistente de conocimiento de trading.
Responde la pregunta del usuario usando ÚNICAMENTE los elementos de evidencia proporcionados.
Para cada afirmación factual, cita el número de fuente como [1] o [2].
Si la evidencia no contiene suficiente información para responder, dilo claramente.
No des consejos financieros ni hagas predicciones de precios futuros.
Resume los patrones e ideas históricas encontradas en la evidencia."""


def _build_context(results: list[dict]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        channel = meta.get("channel", "unknown")
        author = meta.get("author", "")
        ts = meta.get("timestamp", "")
        source_line = f"[{i}] {channel}"
        if author:
            source_line += f" / {author}"
        if ts:
            source_line += f" ({ts[:10]})"
        parts.append(f"{source_line}\n{r['text']}")
    return "\n\n---\n\n".join(parts)


async def synthesize(
    query: str,
    results: list[dict],
    language: str = "en",
) -> tuple[str, list[dict], dict]:
    """
    Returns (answer_text, sources, token_counts).
    sources: list of {index, channel, author, timestamp, stable_id}
    """
    if not results:
        no_data = {
            "en": "No relevant content found in your knowledge base for this query. Try adding more sources or broadening your topic scope.",
            "es": "No se encontró contenido relevante en tu base de conocimiento para esta consulta. Intenta agregar más fuentes o ampliar el alcance temático.",
        }
        return no_data.get(language, no_data["en"]), [], {"input_tokens": 0, "output_tokens": 0}

    context = _build_context(results)
    system_text = _SYSTEM_ES if language == "es" else _SYSTEM_EN

    client = _get_client()
    response = await client.messages.create(
        model=settings.SYNTHESIS_MODEL,
        max_tokens=1024,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": f"Evidence:\n\n{context}\n\nQuestion: {query}",
            }
        ],
    )

    answer = response.content[0].text.strip()
    sources = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        sources.append({
            "index": i,
            "channel": meta.get("channel"),
            "author": meta.get("author"),
            "timestamp": meta.get("timestamp"),
            "stable_id": r.get("source_id"),
            "symbol": meta.get("symbol"),
        })

    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return answer, sources, usage
