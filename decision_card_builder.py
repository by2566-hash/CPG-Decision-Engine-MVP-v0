"""
decision_card_builder.py

Builds merchant-facing decision cards by combining:
  - Action cards from the policy engine  (action_cards_v0.csv)
  - Product replenishment tier metadata  (products_canonical.csv)
  - Playbook evaluation                  (playbook_engine)

Each decision card follows the diagnostic flow:
  Pattern Detected → Evidence Chain → Diagnosis → Likely Causes →
  Checks to Run → Recommendations → Do-Not-Recommend → Expected Impact

Confidence and validation_status are derived from data completeness:
  replenishment_source = "Product"       → confidence=high,   RECOMMENDATION
  replenishment_source = "Aisle"         → confidence=medium, RECOMMENDATION
  replenishment_source = "Department"    → confidence=low,    HYPOTHESIS
  replenishment_source = "Global_Default"→ confidence=low,    HYPOTHESIS

When validation_status == "HYPOTHESIS":
  - Likely causes are labelled [HYPOTHESIS]
  - Recommendations are marked [CONTINGENT ON CHECKS]
  - Automation levels are downgraded to warn_only
  - risk_notes explains the data gap

The policy engine's decision (DISCOUNT / NO_ACTION) is preserved unchanged
in every card under `action_type` / `parameter_value` / `policy_passed`.
"""
from __future__ import annotations

import dataclasses
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from playbook_engine import PlaybookEvaluator, PlaybookLoader
from playbook_engine.models import Playbook
from recommendation_scorer import score_decision_card

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PLAYBOOK_PATH = os.path.join(
    _ROOT, "PLAYBOOKS", "rule_replenishment_discount_v1.yaml"
)

# Maps the replenishment data tier to (confidence, validation_status)
_SOURCE_CONFIDENCE: dict[str, tuple[str, str]] = {
    "Product":        ("high",   "RECOMMENDATION"),
    "Aisle":          ("medium", "RECOMMENDATION"),
    "Department":     ("low",    "HYPOTHESIS"),
    "Global_Default": ("low",    "HYPOTHESIS"),
}

_EVALUATOR = PlaybookEvaluator()


# ---------------------------------------------------------------------------
# Internal helpers  (pure functions — no I/O)
# ---------------------------------------------------------------------------

def _parse_evidence(action_card: dict) -> dict:
    """Return the evidence dict from an action card (evidence may be str or dict)."""
    ev = action_card.get("evidence", {})
    if isinstance(ev, str):
        try:
            return json.loads(ev)
        except (json.JSONDecodeError, ValueError):
            return {}
    return ev or {}


def derive_confidence_and_status(replenishment_source: str) -> tuple[str, str]:
    """
    Derive (confidence, validation_status) from the replenishment data tier.

    Public so tests and callers can use it directly.

    Returns:
        A tuple of (confidence, validation_status) where:
          confidence       ∈ {"high", "medium", "low"}
          validation_status∈ {"RECOMMENDATION", "HYPOTHESIS"}
    """
    return _SOURCE_CONFIDENCE.get(replenishment_source, ("low", "HYPOTHESIS"))


def _annotate_evidence_chain(
    playbook: Playbook,
    evidence: dict,
    replenishment_source: str,
) -> list[dict]:
    """
    Annotate the playbook's evidence chain steps with run-specific observed values.

    The replenishment playbook has 4 evidence steps.  We map the known
    observable signals to the steps in order:
      Step 1 → overdue_ratio (absolute delay)
      Step 2 → overdue_ratio + replenishment_source tier (quality of the cycle estimate)
      Step 3 → inventory_level (fulfillment gate)
      Step 4 → gross_margin_pct (commercial viability)
    """
    overdue_ratio    = evidence.get("overdue_ratio")
    inventory_level  = evidence.get("inventory_level")
    gross_margin_pct = evidence.get("gross_margin_pct")

    annotated = []
    for step in playbook.diagnostic_logic.evidence_chain:
        d = dataclasses.asdict(step)
        if step.step == 1:
            d["observed_value"] = overdue_ratio
        elif step.step == 2:
            d["observed_value"] = (
                f"{overdue_ratio:.2f}x (cycle from {replenishment_source} tier)"
                if overdue_ratio is not None
                else f"cycle from {replenishment_source} tier"
            )
        elif step.step == 3:
            d["observed_value"] = inventory_level
        elif step.step == 4:
            d["observed_value"] = gross_margin_pct
        annotated.append(d)
    return annotated


def _build_diagnosis_summary(
    playbook: Playbook,
    evidence: dict,
    replenishment_source: str,
    validation_status: str,
) -> str:
    """
    Produce a context-specific diagnosis summary for this action card.

    For HYPOTHESIS cards the summary explicitly notes data uncertainty.
    For RECOMMENDATION cards it is the playbook summary enriched with
    the customer/product/overdue specifics.
    """
    customer_id  = evidence.get("customer_id", "unknown")
    product_id   = evidence.get("product_id",  "unknown")
    overdue_ratio = evidence.get("overdue_ratio")
    overdue_pct  = (
        f"{(overdue_ratio - 1.0) * 100:.0f}%"
        if overdue_ratio is not None
        else "an unknown amount"
    )

    if validation_status == "HYPOTHESIS":
        return (
            f"Customer {customer_id} appears overdue for replenishment of product "
            f"{product_id} (~{overdue_pct} past expected cycle). "
            f"HYPOTHESIS ONLY — replenishment cycle estimate is based on "
            f"{replenishment_source}-level data (insufficient product-specific "
            f"purchase history). Complete the diagnostic checks before acting."
        )

    return (
        f"Customer {customer_id} is {overdue_pct} overdue for replenishment of "
        f"product {product_id} "
        f"(overdue_ratio={overdue_ratio:.2f}, cycle measured at {replenishment_source} tier). "
        f"{playbook.diagnosis_summary.strip()}"
    )


def _build_recommendations(
    playbook: Playbook,
    action_card: dict,
    validation_status: str,
) -> list[dict]:
    """
    Build the recommendations list for the decision card.

    HYPOTHESIS mode
      All actions are returned as contingent on completing checks first.
      Automation level is downgraded to warn_only regardless of playbook value.

    RECOMMENDATION mode, policy_passed=True
      Priority-1 slot: DISCOUNT with the specific parameter_value already decided
        by the policy engine (preserves determinism).
      Lower-priority slots: remaining safe actions from the playbook.

    RECOMMENDATION mode, policy_passed=False
      DISCOUNT is omitted (policy blocked it).
      REMINDER_NO_DISCOUNT or any other safe non-discount action is included.

    do_not_recommend actions are always excluded.
    """
    policy_passed   = bool(action_card.get("policy_passed", False))
    action_type     = action_card.get("action_type", "NO_ACTION")
    parameter_value = action_card.get("parameter_value", 0.0)
    forbidden       = playbook.forbidden_actions
    recs: list[dict] = []

    # --- HYPOTHESIS: all actions contingent, automation downgraded ---
    if validation_status == "HYPOTHESIS":
        for action in playbook.safe_suggested_actions:
            recs.append({
                "priority": action.priority,
                "action_type": action.action_type,
                "recommendation": (
                    f"[CONTINGENT ON CHECKS] {action.recommendation.strip()}"
                ),
                "automation_level": "warn_only",
                "preconditions": action.preconditions,
            })
        return recs

    # --- RECOMMENDATION ---
    discount_added = False
    if policy_passed and action_type == "DISCOUNT" and "DISCOUNT" not in forbidden:
        recs.append({
            "priority": 1,
            "action_type": "DISCOUNT",
            "recommendation": (
                f"Offer a {int(parameter_value * 100)}% discount on the overdue product. "
                f"All policy constraints passed (margin and inventory floors met)."
            ),
            "automation_level": "semi_automated",
            "preconditions": ["policy_passed == True"],
        })
        discount_added = True

    for action in playbook.safe_suggested_actions:
        if action.action_type == "DISCOUNT" and (discount_added or not policy_passed):
            continue  # already included the policy-specific version, or policy blocked it
        recs.append({
            "priority": action.priority + (1 if discount_added else 0),
            "action_type": action.action_type,
            "recommendation": action.recommendation.strip(),
            "automation_level": action.automation_level,
            "preconditions": action.preconditions,
        })

    recs.sort(key=lambda x: x["priority"])
    return recs


def _annotate_likely_causes(playbook: Playbook, validation_status: str) -> list[dict]:
    """
    Return likely causes from the playbook, labelled as [HYPOTHESIS] when
    evidence is insufficient to draw a confident conclusion.
    """
    causes = []
    for cause in playbook.likely_causes:
        d = dataclasses.asdict(cause)
        if validation_status == "HYPOTHESIS":
            d["cause"] = f"[HYPOTHESIS] {d['cause']}"
            d["confidence"] = "low"
        causes.append(d)
    return causes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_single_decision_card(
    action_card: dict,
    replenishment_source: str,
    playbook: Playbook,
    run_id: str,
) -> dict:
    """
    Build one merchant-facing decision card from an action card and playbook.

    Pure function — no I/O, fully unit-testable.

    Args:
        action_card:          Raw action card dict from the policy engine.
                              `evidence` may be a JSON string (from CSV) or dict.
        replenishment_source: Replenishment cycle data tier for this product.
                              One of "Product", "Aisle", "Department", "Global_Default".
        playbook:             Loaded Playbook object (replenishment playbook).
        run_id:               Pipeline run identifier for traceability.

    Returns:
        Decision card dict. JSON-serialisable.
    """
    evidence = _parse_evidence(action_card)
    confidence, validation_status = derive_confidence_and_status(replenishment_source)

    # Evaluate playbook trigger against this card's metrics (for diagnostic completeness;
    # the policy engine's decision is authoritative and is preserved unchanged).
    metrics = {
        "overdue_ratio": evidence.get("overdue_ratio", 0.0),
        "mock_inventory": evidence.get("inventory_level", 0),
    }
    _EVALUATOR.evaluate(playbook, metrics)   # result not used to override policy decision

    annotated_chain  = _annotate_evidence_chain(playbook, evidence, replenishment_source)
    diagnosis        = _build_diagnosis_summary(playbook, evidence, replenishment_source, validation_status)
    likely_causes    = _annotate_likely_causes(playbook, validation_status)
    checks           = [dataclasses.asdict(c) for c in playbook.checks_to_run]
    recommendations  = _build_recommendations(playbook, action_card, validation_status)
    do_not_recommend = [dataclasses.asdict(d) for d in playbook.do_not_recommend]
    expected_impact  = dataclasses.asdict(playbook.expected_impact_proxy)

    card = {
        # Identity
        "card_id":        f"dc_{uuid.uuid4().hex[:8]}",
        "action_card_id": action_card.get("id", ""),
        "rule_id":        playbook.rule_id,
        "run_id":         run_id,
        "generated_at":   datetime.now(timezone.utc).isoformat(),

        # Diagnostic flow (the new output)
        "pattern_detected":   playbook.problem_pattern.name,
        "diagnosis_summary":  diagnosis,
        "evidence_chain":     annotated_chain,
        "likely_causes":      likely_causes,
        "checks_to_run":      checks,
        "recommendations":    recommendations,
        "do_not_recommend":   do_not_recommend,
        "why":                playbook.expected_impact_proxy.description.strip(),
        "why_not":            do_not_recommend,
        "risk_notes": (
            [
                f"Replenishment cycle derived from {replenishment_source}-tier data "
                f"(insufficient product-level purchase history). "
                f"Treat diagnosis as hypothesis — validate manually before acting."
            ]
            if validation_status == "HYPOTHESIS"
            else []
        ),

        # Quality signals
        "confidence":        confidence,
        "validation_status": validation_status,
        "expected_impact_proxy": expected_impact,

        # Policy engine decision — unchanged, preserved for traceability
        "policy_passed":    action_card.get("policy_passed", False),
        "action_type":      action_card.get("action_type", "NO_ACTION"),
        "parameter_value":  action_card.get("parameter_value", 0.0),

        # Entity context
        "entity_context": {
            "customer_id":          evidence.get("customer_id", ""),
            "product_id":           evidence.get("product_id", ""),
            "rfm_segment":          evidence.get("rfm_segment", ""),
            "overdue_ratio":        evidence.get("overdue_ratio"),
            "inventory_level":      evidence.get("inventory_level"),
            "replenishment_source": replenishment_source,
        },
    }

    return score_decision_card(card)


def build_decision_cards(
    action_cards_path: str,
    products_path: str,
    output_path: str,
    playbook_path: Optional[str] = None,
    run_id: str = "unknown",
    max_cards: Optional[int] = None,
) -> list[dict]:
    """
    Load action cards and products, build decision cards, write JSONL output.

    Designed to be called as Step 5 of run_pipeline.py immediately after
    the LLM bouncer step has completed.

    Args:
        action_cards_path: Path to action_cards_v0.csv (policy engine output).
        products_path:     Path to products_canonical.csv (compute_risk output).
                           Must contain a `replenishment_source` column.
        output_path:       Destination JSONL path for decision_cards.jsonl.
        playbook_path:     Override path for the replenishment playbook YAML.
                           Defaults to PLAYBOOKS/rule_replenishment_discount_v1.yaml.
        run_id:            Pipeline run identifier (from run_pipeline.py).
        max_cards:         Cap on number of cards to process (useful for testing).

    Returns:
        List of all decision card dicts (also written to output_path).
    """
    loader  = PlaybookLoader()
    playbook = loader.load(playbook_path or DEFAULT_PLAYBOOK_PATH)

    action_cards_df = pd.read_csv(action_cards_path)
    if max_cards is not None:
        action_cards_df = action_cards_df.head(max_cards)

    # Build product_id → replenishment_source lookup from products_canonical
    replenishment_source_map: dict[str, str] = {}
    products_df = pd.read_csv(products_path)
    if "replenishment_source" in products_df.columns:
        replenishment_source_map = dict(
            zip(
                products_df["product_id"].astype(str),
                products_df["replenishment_source"].fillna("Global_Default").astype(str),
            )
        )

    decision_cards: list[dict] = []
    for _, row in action_cards_df.iterrows():
        card = row.to_dict()
        evidence = _parse_evidence(card)
        product_id = str(evidence.get("product_id", row.get("product_id", "")))
        source = replenishment_source_map.get(product_id, "Global_Default")
        dc = build_single_decision_card(card, source, playbook, run_id)
        decision_cards.append(dc)

    with open(output_path, "w") as f:
        for dc in decision_cards:
            f.write(json.dumps(dc) + "\n")

    recs   = sum(1 for dc in decision_cards if dc["validation_status"] == "RECOMMENDATION")
    hypos  = sum(1 for dc in decision_cards if dc["validation_status"] == "HYPOTHESIS")
    print(f"[DecisionCardBuilder] {len(decision_cards)} decision cards → {output_path}")
    print(f"[DecisionCardBuilder]   RECOMMENDATION: {recs}  |  HYPOTHESIS: {hypos}")
    return decision_cards
