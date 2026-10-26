"""
Pattern mining service.

Algorithm:
  1. Aggregate trade_ideas by (symbol_scope, setup_type) using SQL — language-blind
  2. Score each cluster: sample_size, source diversity, author diversity, time spread
  3. For clusters >= MIN_CLUSTER_SIZE: generate strategy_card via Claude
  4. Upsert with versioning (snapshot old state before overwriting)

Cross-source corroboration: unique source_config_ids per cluster weight confidence up.
Per-author reliability: used as a divisor (more unique authors = less concentrated on one voice).
"""
from __future__ import annotations
import json
import logging
import math
import uuid
from decimal import Decimal

import anthropic
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.config import settings
from app.models.strategy_card import StrategyCard
from app.models.analysis_run import RunOutcome
from app.services.usage_service import log_usage_event
from app.services.confidence_tier import compute_confidence_tier

log = logging.getLogger(__name__)

MIN_CLUSTER_SIZE = 3

# A bilingual description plus a four-step flowchart runs to about 600 tokens
# for a small cluster, which is where the cap used to be (#83); 25 sample ideas
# run longer. The cap is a ceiling, not a cost: only what is written is billed.
_MAX_TOKENS = 2000
_FLOWCHART_STEPS = ("entry", "confirmation", "risk_management", "exit")


# A failure every remaining cluster would hit the same way: the run stops on the
# first one and says so once, rather than once per cluster (#86).
_WILL_REPEAT = (anthropic.AuthenticationError, anthropic.PermissionDeniedError)


class CardGenerationFailed(RuntimeError):
    """
    Claude's reply for a cluster cannot be used as a strategy card (#83).

    Carries the reply's token usage: it was billed whether or not it became a card.
    """

    def __init__(self, reason: str, usage: dict | None = None):
        super().__init__(reason)
        self.usage = usage

_SYSTEM = """You are a quantitative trading strategy analyst.
Given a set of trade ideas for a specific setup pattern, produce a concise strategy card.
Return ONLY valid JSON — no markdown, no explanation outside the JSON.

Schema:
{
  "description_en": "2-3 sentences in plain English: what the pattern is, when it appears, what traders are watching for",
  "description_es": "same 2-3 sentences in Spanish",
  "flowchart_spec": {
    "entry": "specific entry trigger (price action, indicator level, candle pattern, etc.)",
    "confirmation": "what to wait for before entering to reduce false signals",
    "risk_management": "stop placement relative to structure; typical R:R implied by the cluster",
    "exit": "target criteria and conditions to exit early"
  }
}"""


async def _generate_card_content(
    symbol_scope: str,
    setup_type: str,
    idea_samples: list[dict],
) -> tuple[dict, dict]:
    """
    Call Claude to generate description + flowchart_spec.
    Returns (fields_dict, token_counts).
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    lines = []
    for s in idea_samples[:25]:  # cap context window
        parts = [f"action={s.get('action', '?')}"]
        if s.get("entry"):
            parts.append(f"entry={s['entry']}")
        if s.get("target"):
            parts.append(f"target={s['target']}")
        if s.get("stop"):
            parts.append(f"stop={s['stop']}")
        summary = s.get("summary_en") or ""
        if summary:
            parts.append(f'summary="{summary[:120]}"')
        lines.append("• " + " | ".join(parts))

    user_content = (
        f"Symbol: {symbol_scope}\n"
        f"Setup type: {setup_type}\n"
        f"Total ideas in cluster: {len(idea_samples)}\n\n"
        f"Sample ideas:\n" + "\n".join(lines)
    )

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=settings.DISTILLATION_MODEL,
        max_tokens=_MAX_TOKENS,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )

    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    if response.stop_reason == "max_tokens":
        raise CardGenerationFailed(f"reply was cut off at max_tokens={_MAX_TOKENS}", usage)

    raw = next((b.text for b in response.content if getattr(b, "text", None)), "").strip()
    if not raw:
        raise CardGenerationFailed("reply has no text", usage)
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()

    try:
        fields = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CardGenerationFailed(f"reply is not valid JSON: {e}", usage) from e
    missing = _what_the_card_is_missing(fields)
    if missing:
        raise CardGenerationFailed(f"reply has no {missing}", usage)

    return fields, usage


def _what_the_card_is_missing(fields) -> str | None:
    """The first part of a card the reply leaves out or leaves empty, if any."""
    def written(value) -> bool:
        return isinstance(value, str) and bool(value.strip())

    if not isinstance(fields, dict):
        return "JSON object"
    for key in ("description_en", "description_es"):
        if not written(fields.get(key)):
            return key
    flowchart = fields.get("flowchart_spec")
    if not isinstance(flowchart, dict):
        return "flowchart_spec"
    for step in _FLOWCHART_STEPS:
        if not written(flowchart.get(step)):
            return f"flowchart_spec {step}"
    return None


def _preliminary_confidence(
    sample_size: int,
    source_count: int,
    unique_authors: int,
    time_span_days: float,
) -> Decimal:
    """
    0–1 score before real win-rate validation (Phase 6).
    Rewards: more samples, more sources, more authors, longer time spread.
    """
    sample_s = min(1.0, sample_size / 20)
    source_s = min(1.0, source_count / 3)
    author_s = min(1.0, unique_authors / 5)
    time_s   = min(1.0, time_span_days / 30)
    score = 0.4 * sample_s + 0.3 * source_s + 0.2 * author_s + 0.1 * time_s
    return Decimal(str(round(score, 4)))


async def run_pattern_mining(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> RunOutcome:
    """
    Mine trade_ideas → create/update strategy_cards.
    Returns what the run did: cards created and updated, clusters that failed,
    and why it stopped if a failure would have repeated for every cluster.
    """
    # Single aggregation query — language-blind, operates only on structured fields
    rows = await db.execute(text("""
        SELECT
            COALESCE(ti.symbol, 'general')                         AS symbol_scope,
            ti.setup_type,
            COUNT(*)                                               AS sample_size,
            COUNT(DISTINCT ei.source_config_id)                    AS source_count,
            COUNT(DISTINCT COALESCE(ti.author, ti.channel))        AS unique_authors,
            MIN(EXTRACT(EPOCH FROM ti.message_timestamp))          AS earliest_epoch,
            MAX(EXTRACT(EPOCH FROM ti.message_timestamp))          AS latest_epoch,
            ARRAY_AGG(ti.id::text ORDER BY ti.message_timestamp DESC) AS idea_ids,
            JSON_AGG(
                JSON_BUILD_OBJECT(
                    'action',     ti.action,
                    'entry',      ti.entry,
                    'target',     ti.target,
                    'stop',       ti.stop,
                    'summary_en', ti.summary_en
                )
                ORDER BY ti.message_timestamp DESC
            )                                                      AS idea_samples
        FROM trade_ideas ti
        JOIN evidence_items ei ON ei.id = ti.evidence_item_id
        WHERE ti.workspace_id = :workspace_id
          AND ti.setup_type IS NOT NULL
          AND ti.action IN ('long', 'short')
        GROUP BY COALESCE(ti.symbol, 'general'), ti.setup_type
        HAVING COUNT(*) >= :min_size
        ORDER BY COUNT(*) DESC
    """), {"workspace_id": str(workspace_id), "min_size": MIN_CLUSTER_SIZE})

    clusters = rows.mappings().all()
    if not clusters:
        log.info("Pattern mining complete: 0 cards touched for workspace %s (no setup type with %d or more trade ideas)",
                 workspace_id, MIN_CLUSTER_SIZE)
        return RunOutcome()

    outcome = RunOutcome()
    if not settings.ANTHROPIC_API_KEY:
        outcome.stopped_because = "ANTHROPIC_API_KEY is not set, so no strategy card can be written."
        log.error("Pattern mining stopped for workspace %s: %s", workspace_id, outcome.stopped_because)
        return outcome

    total_input_tokens = 0
    total_output_tokens = 0

    for cluster in clusters:
        symbol_scope = cluster["symbol_scope"]
        setup_type = cluster["setup_type"]
        sample_size = int(cluster["sample_size"])
        source_count = int(cluster["source_count"])
        unique_authors = int(cluster["unique_authors"])

        earliest = cluster["earliest_epoch"]
        latest   = cluster["latest_epoch"]
        time_span_days = (float(latest) - float(earliest)) / 86400 if earliest and latest else 0

        idea_ids: list[str] = list(cluster["idea_ids"] or [])
        idea_samples: list[dict] = list(cluster["idea_samples"] or [])

        confidence = _preliminary_confidence(sample_size, source_count, unique_authors, time_span_days)

        fields = None
        try:
            fields, usage = await _generate_card_content(symbol_scope, setup_type, idea_samples)
        except CardGenerationFailed as e:
            log.error("Card generation failed for %s/%s: %s", symbol_scope, setup_type, e)
            outcome.failures.append({"subject": f"{symbol_scope}/{setup_type}", "reason": str(e)})
            # A reply that was generated is billed even when it is not a card.
            usage = e.usage
        except _WILL_REPEAT as e:
            outcome.stopped_because = (
                f"Anthropic refused the request ({e.status_code}): the API key is invalid or has no "
                "access to the model, so no strategy card can be written. "
                "Check ANTHROPIC_API_KEY and restart the backend."
            )
            log.error("Pattern mining stopped for workspace %s: %s", workspace_id, e)
            break
        except Exception as e:
            log.error("Card generation failed for %s/%s: %s", symbol_scope, setup_type, e)
            outcome.failures.append({"subject": f"{symbol_scope}/{setup_type}", "reason": str(e)})
            continue

        if usage:
            total_input_tokens  += usage["input_tokens"]
            total_output_tokens += usage["output_tokens"]
        if fields is None:
            continue

        # Upsert strategy_card
        existing_result = await db.execute(
            select(StrategyCard).where(
                StrategyCard.workspace_id == workspace_id,
                StrategyCard.symbol_scope == symbol_scope,
                StrategyCard.setup_type == setup_type,
            )
        )
        card = existing_result.scalar_one_or_none()

        tier = compute_confidence_tier(sample_size, getattr(card, "win_rate", None) if card else None, getattr(card, "confidence_interval", None) if card else None)

        if card is None:
            card = StrategyCard(
                workspace_id=workspace_id,
                symbol_scope=symbol_scope,
                setup_type=setup_type,
                description_en=fields["description_en"],
                description_es=fields["description_es"],
                flowchart_spec=fields["flowchart_spec"],
                supporting_evidence=idea_ids,
                sample_size=sample_size,
                preliminary_confidence=confidence,
                source_count=source_count,
                confidence_tier=tier,
                version=1,
                version_history=[],
                last_updated=now_utc(),
            )
            db.add(card)
            outcome.cards_created += 1
        else:
            prev_tier = card.confidence_tier or "still_learning"
            changes_parts = []
            if card.sample_size != sample_size:
                changes_parts.append(f"sample_size {card.sample_size} → {sample_size}")
            if card.source_count != source_count:
                changes_parts.append(f"sources {card.source_count} → {source_count}")
            if prev_tier != tier:
                changes_parts.append(f"tier {prev_tier} → {tier}")
            changes_en = "; ".join(changes_parts) if changes_parts else "Periodic recomputation"

            # Snapshot current state before overwriting
            snapshot = {
                "version": card.version,
                # last_updated is NOT NULL, so the fallback was unreachable and
                # only existed to put an unreadable value in the history.
                "snapshot_at": (card.last_updated or now_utc()).isoformat(),
                "change_source": "mining",
                "sample_size": card.sample_size,
                "win_rate": str(card.win_rate) if card.win_rate is not None else None,
                "confidence_tier": prev_tier,
                "preliminary_confidence": str(card.preliminary_confidence),
                "changes_en": changes_en,
            }
            card.version_history = (card.version_history or []) + [snapshot]
            card.version += 1
            card.description_en = fields["description_en"]
            card.description_es = fields["description_es"]
            card.flowchart_spec = fields["flowchart_spec"]
            card.supporting_evidence = idea_ids
            card.sample_size = sample_size
            card.preliminary_confidence = confidence
            card.source_count = source_count
            card.confidence_tier = tier
            card.last_updated = now_utc()
            outcome.cards_updated += 1

    await db.flush()

    # Log aggregated LLM cost for the whole mining run
    if total_input_tokens > 0:
        cost = Decimal(str(0.000003 * total_input_tokens + 0.000015 * total_output_tokens))
        await log_usage_event(
            workspace_id=workspace_id,
            task_type="pattern_mining",
            provider_model=settings.DISTILLATION_MODEL,
            input_units=total_input_tokens,
            output_units=total_output_tokens,
            cost_usd=cost,
        )

    await db.commit()
    log.info("Pattern mining complete: %d cards touched for workspace %s",
             outcome.cards_created + outcome.cards_updated, workspace_id)
    return outcome
