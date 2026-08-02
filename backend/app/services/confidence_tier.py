"""
Shared confidence tiering logic.

Tiers:
  still_learning — fewer than 5 samples, OR no validated win rate yet.
                   Pattern exists but we don't have enough data to trust it.
  developing     — 5-14 samples, OR CI is wide (> 0.30). Shows promise but
                   needs more evidence before acting on it.
  established    — 15+ samples, win rate is available, and CI width ≤ 0.30.
                   Statistically meaningful enough to inform real decisions.

These thresholds are conservative by design — the spec says to show
failure cases, not just wins, and to be honest about uncertainty.
"""
from decimal import Decimal

STILL_LEARNING_MIN_SAMPLES = 5
ESTABLISHED_MIN_SAMPLES = 15
WIDE_CI_THRESHOLD = 0.30


def compute_confidence_tier(
    sample_size: int,
    win_rate: Decimal | None,
    confidence_interval: dict | None,
) -> str:
    if sample_size < STILL_LEARNING_MIN_SAMPLES or win_rate is None:
        return "still_learning"

    ci_width = 1.0
    if confidence_interval:
        ci_width = float(confidence_interval.get("upper", 1.0)) - float(confidence_interval.get("lower", 0.0))

    if sample_size >= ESTABLISHED_MIN_SAMPLES and ci_width <= WIDE_CI_THRESHOLD:
        return "established"

    return "developing"


def tier_label_en(tier: str) -> str:
    return {"still_learning": "Still learning", "developing": "Developing", "established": "Established"}.get(tier, tier)


def tier_label_es(tier: str) -> str:
    return {"still_learning": "Aprendiendo", "developing": "En desarrollo", "established": "Establecida"}.get(tier, tier)
