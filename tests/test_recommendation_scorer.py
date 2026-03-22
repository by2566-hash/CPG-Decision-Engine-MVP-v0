"""
tests/test_recommendation_scorer.py

Tests for recommendation_scorer.py — all pure functions, no I/O.

Coverage:
  A. Individual dimension helpers
  B. score_decision_card — field presence, value ranges, tier assignment
  C. Recommendation-level quality_score annotation
  D. Re-sort behaviour within a card
  E. score_and_rank — cross-card ordering
  F. Edge cases — missing fields, empty recs, unknown segment/tier
  G. Integration — score survives build_single_decision_card
"""
from __future__ import annotations

import pytest
from pathlib import Path

from recommendation_scorer import (
    _confidence_score,
    _diagnostic_quality_score,
    _execution_cost_score,
    _impact_proxy_score,
    _specificity_score,
    _to_quality_tier,
    score_and_rank,
    score_decision_card,
)

# ---------------------------------------------------------------------------
# Minimal card factory
# ---------------------------------------------------------------------------

def _card(
    *,
    replenishment_source: str = "Product",
    overdue_ratio: float | None = 1.5,
    rfm_segment: str = "Champions",
    validation_status: str = "RECOMMENDATION",
    confidence: str = "high",
    recs: list[dict] | None = None,
) -> dict:
    if recs is None:
        recs = [
            {"priority": 1, "action_type": "DISCOUNT",
             "recommendation": "Offer discount.", "automation_level": "semi_automated",
             "preconditions": []},
            {"priority": 2, "action_type": "REMINDER_NO_DISCOUNT",
             "recommendation": "Send reminder.", "automation_level": "warn_only",
             "preconditions": []},
        ]
    return {
        "card_id": "dc_test",
        "action_card_id": "ac_test",
        "rule_id": "rule_replenishment_discount_v1",
        "validation_status": validation_status,
        "confidence": confidence,
        "entity_context": {
            "customer_id": "C001",
            "product_id": "P001",
            "rfm_segment": rfm_segment,
            "overdue_ratio": overdue_ratio,
            "inventory_level": 100,
            "replenishment_source": replenishment_source,
        },
        "recommendations": list(recs),  # copy so tests don't share state
    }


# ---------------------------------------------------------------------------
# A. Dimension helpers
# ---------------------------------------------------------------------------

class TestSpecificityScore:
    def test_product_tier(self):       assert _specificity_score("Product") == 30
    def test_aisle_tier(self):         assert _specificity_score("Aisle") == 20
    def test_department_tier(self):    assert _specificity_score("Department") == 10
    def test_global_default_tier(self): assert _specificity_score("Global_Default") == 5
    def test_unknown_tier(self):        assert _specificity_score("Unknown") == 5


class TestDiagnosticQualityScore:
    def test_above_2x(self):     assert _diagnostic_quality_score(2.1) == 25
    def test_exactly_2x(self):   assert _diagnostic_quality_score(2.0) == 25
    def test_1_5_to_2x(self):    assert _diagnostic_quality_score(1.7) == 20
    def test_exactly_1_5(self):  assert _diagnostic_quality_score(1.5) == 20
    def test_1_2_to_1_5(self):   assert _diagnostic_quality_score(1.3) == 15
    def test_exactly_1_2(self):  assert _diagnostic_quality_score(1.2) == 15
    def test_below_threshold(self): assert _diagnostic_quality_score(1.0) == 5
    def test_none(self):         assert _diagnostic_quality_score(None) == 5


class TestImpactProxyScore:
    def test_champions(self):         assert _impact_proxy_score("Champions") == 25
    def test_loyal(self):             assert _impact_proxy_score("Loyal") == 25
    def test_loyal_customers(self):   assert _impact_proxy_score("Loyal Customers") == 25
    def test_champion_slash_loyal(self): assert _impact_proxy_score("Champion / Loyal") == 25
    def test_at_risk(self):           assert _impact_proxy_score("At Risk") == 20
    def test_at_risk_hyphen(self):    assert _impact_proxy_score("At-Risk") == 20
    def test_needs_attention(self):   assert _impact_proxy_score("Needs Attention") == 15
    def test_promising(self):         assert _impact_proxy_score("Promising") == 15
    def test_new_customers(self):     assert _impact_proxy_score("New Customers") == 12
    def test_hibernating(self):       assert _impact_proxy_score("Hibernating") == 10
    def test_lost(self):              assert _impact_proxy_score("Lost") == 10
    def test_cannot_lose_them(self):  assert _impact_proxy_score("Cannot Lose Them") == 20
    def test_unknown_segment(self):   assert _impact_proxy_score("Tier X") == 10
    def test_empty_segment(self):     assert _impact_proxy_score("") == 10
    def test_case_insensitive(self):  assert _impact_proxy_score("CHAMPIONS") == 25


class TestExecutionCostScore:
    def test_semi_automated(self):  assert _execution_cost_score("semi_automated") == 10
    def test_warn_only(self):       assert _execution_cost_score("warn_only") == 7
    def test_manual(self):          assert _execution_cost_score("manual") == 5
    def test_unknown(self):         assert _execution_cost_score("unknown") == 5


class TestConfidenceScore:
    def test_recommendation_high(self):   assert _confidence_score("RECOMMENDATION", "high") == 10
    def test_recommendation_medium(self): assert _confidence_score("RECOMMENDATION", "medium") == 8
    def test_recommendation_low(self):    assert _confidence_score("RECOMMENDATION", "low") == 6
    def test_hypothesis_any(self):        assert _confidence_score("HYPOTHESIS", "high") == 3
    def test_hypothesis_medium(self):     assert _confidence_score("HYPOTHESIS", "medium") == 3
    def test_hypothesis_low(self):        assert _confidence_score("HYPOTHESIS", "low") == 3
    def test_unknown_combo(self):         assert _confidence_score("UNKNOWN", "high") == 3


class TestQualityTier:
    def test_tier_a_at_boundary(self):  assert _to_quality_tier(0.70) == "A"
    def test_tier_a_above(self):        assert _to_quality_tier(0.95) == "A"
    def test_tier_b_at_boundary(self):  assert _to_quality_tier(0.50) == "B"
    def test_tier_b_below_a(self):      assert _to_quality_tier(0.65) == "B"
    def test_tier_c_at_boundary(self):  assert _to_quality_tier(0.00) == "C"
    def test_tier_c_below_b(self):      assert _to_quality_tier(0.45) == "C"


# ---------------------------------------------------------------------------
# B. score_decision_card — field presence, value range, tier
# ---------------------------------------------------------------------------

class TestScoreDecisionCard:
    def test_quality_score_present(self):
        card = score_decision_card(_card())
        assert "quality_score" in card

    def test_quality_tier_present(self):
        card = score_decision_card(_card())
        assert "quality_tier" in card

    def test_quality_breakdown_present(self):
        card = score_decision_card(_card())
        assert "quality_breakdown" in card

    def test_quality_breakdown_has_all_dimensions(self):
        card = score_decision_card(_card())
        bd = card["quality_breakdown"]
        for key in ["specificity", "diagnostic_quality", "impact_proxy", "confidence"]:
            assert key in bd

    def test_quality_score_in_range(self):
        card = score_decision_card(_card())
        assert 0.0 <= card["quality_score"] <= 1.0

    def test_high_confidence_product_tier_is_tier_a(self):
        card = score_decision_card(_card(
            replenishment_source="Product",
            overdue_ratio=1.8,
            rfm_segment="Champions",
            validation_status="RECOMMENDATION",
            confidence="high",
        ))
        assert card["quality_tier"] == "A"

    def test_hypothesis_global_default_is_tier_c(self):
        card = score_decision_card(_card(
            replenishment_source="Global_Default",
            overdue_ratio=1.2,
            rfm_segment="Hibernating",
            validation_status="HYPOTHESIS",
            confidence="low",
        ))
        assert card["quality_tier"] == "C"

    def test_score_higher_for_product_than_global_default(self):
        high = score_decision_card(_card(replenishment_source="Product"))
        low  = score_decision_card(_card(replenishment_source="Global_Default"))
        assert high["quality_score"] > low["quality_score"]

    def test_score_higher_for_champions_than_lost(self):
        high = score_decision_card(_card(rfm_segment="Champions"))
        low  = score_decision_card(_card(rfm_segment="Lost"))
        assert high["quality_score"] > low["quality_score"]

    def test_score_higher_for_strong_overdue_ratio(self):
        high = score_decision_card(_card(overdue_ratio=2.5))
        low  = score_decision_card(_card(overdue_ratio=1.2))
        assert high["quality_score"] > low["quality_score"]

    def test_score_higher_for_recommendation_than_hypothesis(self):
        rec  = score_decision_card(_card(validation_status="RECOMMENDATION", confidence="high"))
        hypo = score_decision_card(_card(validation_status="HYPOTHESIS", confidence="low"))
        assert rec["quality_score"] > hypo["quality_score"]

    def test_returns_same_card_object(self):
        card = _card()
        result = score_decision_card(card)
        assert result is card

    def test_empty_recs_gives_zero_score(self):
        card = score_decision_card(_card(recs=[]))
        assert card["quality_score"] == 0.0

    def test_breakdown_specificity_matches_source(self):
        card = score_decision_card(_card(replenishment_source="Aisle"))
        assert card["quality_breakdown"]["specificity"] == 20

    def test_breakdown_diagnostic_matches_ratio(self):
        card = score_decision_card(_card(overdue_ratio=2.1))
        assert card["quality_breakdown"]["diagnostic_quality"] == 25


# ---------------------------------------------------------------------------
# C. Recommendation-level quality_score annotation
# ---------------------------------------------------------------------------

class TestRecommendationLevelScore:
    def test_each_rec_has_quality_score(self):
        card = score_decision_card(_card())
        for rec in card["recommendations"]:
            assert "quality_score" in rec

    def test_rec_quality_score_in_range(self):
        card = score_decision_card(_card())
        for rec in card["recommendations"]:
            assert 0.0 <= rec["quality_score"] <= 1.0

    def test_semi_automated_scores_higher_than_warn_only(self):
        card = score_decision_card(_card())
        recs = {r["automation_level"]: r["quality_score"] for r in card["recommendations"]}
        assert recs["semi_automated"] > recs["warn_only"]

    def test_card_score_equals_best_rec_score(self):
        card = score_decision_card(_card())
        best = max(r["quality_score"] for r in card["recommendations"])
        assert card["quality_score"] == best


# ---------------------------------------------------------------------------
# D. Re-sort behaviour
# ---------------------------------------------------------------------------

class TestResortBehaviour:
    def test_priority_1_still_first_after_scoring(self):
        card = score_decision_card(_card())
        assert card["recommendations"][0]["priority"] == 1

    def test_within_same_priority_higher_score_comes_first(self):
        recs = [
            {"priority": 2, "action_type": "REMINDER_NO_DISCOUNT",
             "recommendation": "a", "automation_level": "warn_only", "preconditions": []},
            {"priority": 2, "action_type": "MANUAL_OUTREACH",
             "recommendation": "b", "automation_level": "manual", "preconditions": []},
        ]
        card = score_decision_card(_card(recs=recs))
        scores = [r["quality_score"] for r in card["recommendations"]]
        assert scores[0] >= scores[1]


# ---------------------------------------------------------------------------
# E. score_and_rank — cross-card ordering
# ---------------------------------------------------------------------------

class TestScoreAndRank:
    def test_returns_list(self):
        cards = [_card(), _card()]
        result = score_and_rank(cards)
        assert isinstance(result, list)

    def test_all_cards_scored(self):
        cards = [_card(), _card()]
        result = score_and_rank(cards)
        for card in result:
            assert "quality_score" in card

    def test_sorted_descending(self):
        high = _card(replenishment_source="Product", rfm_segment="Champions",
                     overdue_ratio=2.0, validation_status="RECOMMENDATION", confidence="high")
        low  = _card(replenishment_source="Global_Default", rfm_segment="Lost",
                     overdue_ratio=1.2, validation_status="HYPOTHESIS", confidence="low")
        result = score_and_rank([low, high])
        assert result[0]["quality_score"] >= result[1]["quality_score"]

    def test_empty_list(self):
        assert score_and_rank([]) == []

    def test_single_card(self):
        result = score_and_rank([_card()])
        assert len(result) == 1
        assert "quality_score" in result[0]


# ---------------------------------------------------------------------------
# F. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_entity_context(self):
        card = {"card_id": "x", "validation_status": "HYPOTHESIS",
                "confidence": "low", "recommendations": []}
        # Should not raise
        result = score_decision_card(card)
        assert result["quality_score"] == 0.0

    def test_none_overdue_ratio(self):
        card = score_decision_card(_card(overdue_ratio=None))
        assert card["quality_breakdown"]["diagnostic_quality"] == 5

    def test_unknown_replenishment_source(self):
        card = score_decision_card(_card(replenishment_source="Exotic_Tier"))
        assert card["quality_breakdown"]["specificity"] == 5

    def test_unknown_rfm_segment(self):
        card = score_decision_card(_card(rfm_segment="Segment_XYZ"))
        assert card["quality_breakdown"]["impact_proxy"] == 10

    def test_score_is_rounded_to_4dp(self):
        card = score_decision_card(_card())
        score = card["quality_score"]
        assert score == round(score, 4)


# ---------------------------------------------------------------------------
# G. Integration — score survives build_single_decision_card
# ---------------------------------------------------------------------------

class TestIntegrationWithBuilder:
    def test_build_single_decision_card_has_quality_score(self):
        from pathlib import Path
        from playbook_engine import PlaybookLoader
        from decision_card_builder import build_single_decision_card

        loader = PlaybookLoader()
        pb = loader.load(str(Path(__file__).parent.parent / "PLAYBOOKS" / "rule_replenishment_discount_v1.yaml"))

        ac = {
            "id": "ac_integ",
            "action_type": "DISCOUNT",
            "parameter_value": 0.15,
            "policy_passed": True,
            "evidence": {
                "customer_id": "C001", "product_id": "P001",
                "overdue_ratio": 1.7, "inventory_level": 100,
                "gross_margin_pct": 0.30, "rfm_segment": "Champions",
            },
        }
        card = build_single_decision_card(ac, "Product", pb, "run_integ")
        assert "quality_score" in card
        assert "quality_tier" in card
        assert "quality_breakdown" in card
        assert 0.0 <= card["quality_score"] <= 1.0

    def test_build_single_decision_card_recs_have_quality_score(self):
        from pathlib import Path
        from playbook_engine import PlaybookLoader
        from decision_card_builder import build_single_decision_card

        loader = PlaybookLoader()
        pb = loader.load(str(Path(__file__).parent.parent / "PLAYBOOKS" / "rule_replenishment_discount_v1.yaml"))

        ac = {
            "id": "ac_integ2",
            "action_type": "DISCOUNT",
            "parameter_value": 0.15,
            "policy_passed": True,
            "evidence": {
                "customer_id": "C002", "product_id": "P002",
                "overdue_ratio": 1.5, "inventory_level": 80,
                "gross_margin_pct": 0.25, "rfm_segment": "Loyal",
            },
        }
        card = build_single_decision_card(ac, "Aisle", pb, "run_integ2")
        for rec in card["recommendations"]:
            assert "quality_score" in rec

    def test_hypothesis_card_has_lower_score_than_recommendation(self):
        from pathlib import Path
        from playbook_engine import PlaybookLoader
        from decision_card_builder import build_single_decision_card

        loader = PlaybookLoader()
        pb = loader.load(str(Path(__file__).parent.parent / "PLAYBOOKS" / "rule_replenishment_discount_v1.yaml"))

        def _make(source):
            ac = {
                "id": "ac_x",
                "action_type": "DISCOUNT",
                "parameter_value": 0.15,
                "policy_passed": True,
                "evidence": {
                    "customer_id": "C001", "product_id": "P001",
                    "overdue_ratio": 1.5, "inventory_level": 100,
                    "gross_margin_pct": 0.30, "rfm_segment": "Champions",
                },
            }
            return build_single_decision_card(ac, source, pb, "run_x")

        rec_card  = _make("Product")        # → RECOMMENDATION, high confidence
        hypo_card = _make("Global_Default") # → HYPOTHESIS, low confidence
        assert rec_card["quality_score"] > hypo_card["quality_score"]
