"""
recommendation_scorer.py

Lightweight, interpretable quality scoring for merchant-facing decision cards.

SCORING DIMENSIONS
==================

Each dimension produces a sub-score (integer). The dimensions sum to 100 points
and are divided by 100 to yield a final quality_score in [0.0, 1.0].

  SPECIFICITY         (0–30)  — data-tier quality of the replenishment cycle
  DIAGNOSTIC_QUALITY  (0–25)  — magnitude of the overdue signal
  IMPACT_PROXY        (0–25)  — estimated business value by RFM segment
  EXECUTION_COST      (0–10)  — effort to execute (semi_automated = low cost)
  CONFIDENCE          (0–10)  — RECOMMENDATION vs HYPOTHESIS + confidence tier

All signals come from fields already present in the decision card; no external
data is fetched and no values are invented.

QUALITY TIERS
=============
  A  (quality_score ≥ 0.70)  — Strong signal, act promptly
  B  (quality_score ≥ 0.50)  — Reasonable signal, review then act
  C  (quality_score  < 0.50)  — Weak signal, validate first

RECOMMENDATION-LEVEL SCORE
===========================
Each recommendation in a card inherits the card's SPECIFICITY, DIAGNOSTIC_QUALITY,
IMPACT_PROXY, and CONFIDENCE scores, but uses its own EXECUTION_COST derived from
its automation_level. This lets different actions on the same card carry different
scores (e.g. semi_automated DISCOUNT outscores warn_only REMINDER on the same card).

CARD-LEVEL SCORE
================
The card quality_score is the score of its highest-scoring recommendation.
If the card has no recommendations, the score is 0.0.

OUTPUT CONTRACT
===============
score_decision_card(card) → card (mutates in-place and returns)
  Adds to the card:
    card["quality_score"]     : float in [0.0, 1.0]
    card["quality_tier"]      : "A" | "B" | "C"
    card["quality_breakdown"] : dict of per-dimension sub-scores
  Adds to each recommendation:
    rec["quality_score"]      : float in [0.0, 1.0]
  Recommendations are re-sorted by quality_score descending as a secondary
  key (primary key remains the existing priority).

score_and_rank(cards) → list[dict]
  Scores all cards and returns them sorted by quality_score descending.
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Dimension lookup tables (all values are integers that sum to 100)
# ---------------------------------------------------------------------------

# SPECIFICITY: how directly the replenishment cycle was measured for this product.
_SPECIFICITY: dict[str, int] = {
    "Product":        30,   # direct product-level interpurchase history (≥5 gaps)
    "Aisle":          20,   # aisle-level median (sparse product history)
    "Department":     10,   # department-level median (very sparse)
    "Global_Default":  5,   # global fallback (no useful product signal)
}

# DIAGNOSTIC_QUALITY: magnitude of the overdue signal — stronger overdue = clearer need.
# Thresholds are (minimum_ratio, score) pairs; matched top-to-bottom.
_DIAGNOSTIC_THRESHOLDS: list[tuple[float, int]] = [
    (2.0, 25),   # customer is ≥2× past their replenishment window — high urgency
    (1.5, 20),   # 1.5–2×: clear delay, meaningful signal
    (1.2, 15),   # 1.2–1.5×: at threshold, mild signal
    (0.0,  5),   # below 1.2 or missing — at/below threshold, weak signal
]

# IMPACT_PROXY: segment-level LTV signal.  Keys are normalised (lower-case, stripped).
# Derived from the rfm_segment field already computed by compute_rfm.py.
_IMPACT_PROXY: dict[str, int] = {
    # Highest LTV — highest priority for retention spend
    "champions":              25,
    "loyal":                  25,
    "loyal customers":        25,
    "champion / loyal":       25,
    # High value, churn risk is the signal
    "at risk":                20,
    "at-risk":                20,
    "at_risk":                20,
    # Medium value
    "needs attention":        15,
    "promising":              15,
    "new customers":          12,
    # Low-signal segments — still worth acting on but lower expected return
    "hibernating":            10,
    "lost":                   10,
    "cannot lose them":       20,   # special high-value lapsed segment
}
_IMPACT_PROXY_DEFAULT = 10   # unknown / unmapped segment

# EXECUTION_COST: effort required to execute the recommendation.
# Higher score = lower execution cost (more immediately actionable).
_EXECUTION_COST: dict[str, int] = {
    "semi_automated": 10,   # system executes with human review at batch level
    "warn_only":       7,   # human reviews each record before sending
    "manual":          5,   # fully manual — only use when signal is very clear
}
_EXECUTION_COST_DEFAULT = 5

# CONFIDENCE: combined validation_status + confidence tier.
_CONFIDENCE: dict[tuple[str, str], int] = {
    ("RECOMMENDATION", "high"):   10,
    ("RECOMMENDATION", "medium"):  8,
    ("RECOMMENDATION", "low"):     6,
    ("HYPOTHESIS",     "high"):    3,
    ("HYPOTHESIS",     "medium"):  3,
    ("HYPOTHESIS",     "low"):     3,
}
_CONFIDENCE_DEFAULT = 3

# Quality tier boundaries (inclusive lower bound)
_TIER_BOUNDS: list[tuple[float, str]] = [
    (0.70, "A"),   # Act promptly
    (0.50, "B"),   # Review then act
    (0.00, "C"),   # Validate first
]


# ---------------------------------------------------------------------------
# Internal helpers (pure functions)
# ---------------------------------------------------------------------------

def _specificity_score(replenishment_source: str) -> int:
    return _SPECIFICITY.get(replenishment_source, 5)


def _diagnostic_quality_score(overdue_ratio: Optional[float]) -> int:
    if overdue_ratio is None:
        return 5
    for threshold, score in _DIAGNOSTIC_THRESHOLDS:
        if overdue_ratio >= threshold:
            return score
    return 5


def _impact_proxy_score(rfm_segment: str) -> int:
    key = rfm_segment.strip().lower()
    return _IMPACT_PROXY.get(key, _IMPACT_PROXY_DEFAULT)


def _execution_cost_score(automation_level: str) -> int:
    return _EXECUTION_COST.get(automation_level, _EXECUTION_COST_DEFAULT)


def _confidence_score(validation_status: str, confidence: str) -> int:
    return _CONFIDENCE.get((validation_status, confidence), _CONFIDENCE_DEFAULT)


def _to_quality_tier(score: float) -> str:
    for bound, tier in _TIER_BOUNDS:
        if score >= bound:
            return tier
    return "C"


def _score_single_recommendation(
    rec: dict,
    specificity: int,
    diagnostic_quality: int,
    impact_proxy: int,
    confidence: int,
) -> float:
    """Score one recommendation using card-level signals + rec-level automation cost."""
    exec_cost = _execution_cost_score(rec.get("automation_level", ""))
    raw = specificity + diagnostic_quality + impact_proxy + exec_cost + confidence
    return round(raw / 100.0, 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_decision_card(card: dict) -> dict:
    """
    Score a decision card and annotate it with quality signals.

    Mutates the card in-place (adds quality_score, quality_tier, quality_breakdown
    to the card; adds quality_score to each recommendation).  Returns the card.

    Args:
        card: A decision card dict as produced by decision_card_builder.

    Returns:
        The same card dict, annotated with quality scores.
    """
    entity = card.get("entity_context", {})

    specificity      = _specificity_score(entity.get("replenishment_source", "Global_Default"))
    diagnostic       = _diagnostic_quality_score(entity.get("overdue_ratio"))
    impact           = _impact_proxy_score(entity.get("rfm_segment", ""))
    conf             = _confidence_score(
        card.get("validation_status", "HYPOTHESIS"),
        card.get("confidence", "low"),
    )

    # Score each recommendation and annotate it
    recs = card.get("recommendations", [])
    for rec in recs:
        rec["quality_score"] = _score_single_recommendation(
            rec, specificity, diagnostic, impact, conf
        )

    # Re-sort: primary = priority (ascending), secondary = quality_score (descending)
    recs.sort(key=lambda r: (r.get("priority", 99), -r.get("quality_score", 0.0)))

    # Card-level score = best recommendation score (or 0.0 if no recs)
    card_score = max((r["quality_score"] for r in recs), default=0.0)

    card["quality_score"] = card_score
    card["quality_tier"] = _to_quality_tier(card_score)
    card["quality_breakdown"] = {
        "specificity":       specificity,
        "diagnostic_quality": diagnostic,
        "impact_proxy":      impact,
        "confidence":        conf,
        # execution_cost is per-recommendation; shown on each rec's quality_score
    }

    return card


def score_and_rank(cards: list[dict]) -> list[dict]:
    """
    Score all cards and return them sorted by quality_score descending.

    Args:
        cards: List of decision card dicts.

    Returns:
        The same list, each card annotated with quality fields, sorted by
        quality_score descending (highest-priority recommendations first).
    """
    for card in cards:
        score_decision_card(card)
    cards.sort(key=lambda c: c.get("quality_score", 0.0), reverse=True)
    return cards
