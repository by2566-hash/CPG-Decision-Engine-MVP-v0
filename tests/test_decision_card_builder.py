"""
tests/test_decision_card_builder.py

Fixture-based tests for decision_card_builder.py.

Coverage:
  A. derive_confidence_and_status — all tiers + unknown fallback
  B. build_single_decision_card — RECOMMENDATION/high, RECOMMENDATION/medium,
     HYPOTHESIS, policy_passed=False, evidence as JSON string
  C. Evidence chain annotation — observed values mapped to steps 1–4
  D. Diagnosis summary — HYPOTHESIS note vs. RECOMMENDATION copy
  E. Recommendations — all three modes (HYPOTHESIS, policy passed, policy blocked)
  F. Likely causes — HYPOTHESIS prefix + confidence downgrade
  G. build_decision_cards — file-level integration with CSV fixtures
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

import pytest

from decision_card_builder import (
    build_decision_cards,
    build_single_decision_card,
    derive_confidence_and_status,
)
from playbook_engine import PlaybookLoader

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent
_PLAYBOOK_PATH = str(_ROOT / "PLAYBOOKS" / "rule_replenishment_discount_v1.yaml")


@pytest.fixture(scope="module")
def playbook():
    loader = PlaybookLoader()
    return loader.load(_PLAYBOOK_PATH)


def _make_action_card(
    *,
    product_id: str = "P001",
    customer_id: str = "C001",
    overdue_ratio: float = 1.5,
    inventory_level: int = 120,
    gross_margin_pct: float = 0.30,
    rfm_segment: str = "Champion / Loyal",
    policy_passed: bool = True,
    action_type: str = "DISCOUNT",
    parameter_value: float = 0.15,
    evidence_as_string: bool = False,
) -> dict:
    evidence = {
        "customer_id": customer_id,
        "product_id": product_id,
        "overdue_ratio": overdue_ratio,
        "inventory_level": inventory_level,
        "gross_margin_pct": gross_margin_pct,
        "rfm_segment": rfm_segment,
    }
    return {
        "id": "act_test_001",
        "action_type": action_type,
        "parameter_value": parameter_value,
        "policy_passed": policy_passed,
        "evidence": json.dumps(evidence) if evidence_as_string else evidence,
    }


# ---------------------------------------------------------------------------
# A. derive_confidence_and_status
# ---------------------------------------------------------------------------

class TestDeriveConfidenceAndStatus:
    def test_product_tier_is_high_recommendation(self):
        conf, status = derive_confidence_and_status("Product")
        assert conf == "high"
        assert status == "RECOMMENDATION"

    def test_aisle_tier_is_medium_recommendation(self):
        conf, status = derive_confidence_and_status("Aisle")
        assert conf == "medium"
        assert status == "RECOMMENDATION"

    def test_department_tier_is_low_hypothesis(self):
        conf, status = derive_confidence_and_status("Department")
        assert conf == "low"
        assert status == "HYPOTHESIS"

    def test_global_default_tier_is_low_hypothesis(self):
        conf, status = derive_confidence_and_status("Global_Default")
        assert conf == "low"
        assert status == "HYPOTHESIS"

    def test_unknown_tier_falls_back_to_low_hypothesis(self):
        conf, status = derive_confidence_and_status("Unknown_Tier")
        assert conf == "low"
        assert status == "HYPOTHESIS"

    def test_empty_string_falls_back_to_low_hypothesis(self):
        conf, status = derive_confidence_and_status("")
        assert conf == "low"
        assert status == "HYPOTHESIS"


# ---------------------------------------------------------------------------
# B. build_single_decision_card — card structure
# ---------------------------------------------------------------------------

class TestBuildSingleDecisionCardStructure:
    """Verify the returned card has all required top-level fields."""

    REQUIRED_FIELDS = [
        "card_id", "action_card_id", "rule_id", "run_id", "generated_at",
        "pattern_detected", "diagnosis_summary", "evidence_chain",
        "likely_causes", "checks_to_run", "recommendations", "do_not_recommend",
        "why", "why_not", "risk_notes",
        "confidence", "validation_status", "expected_impact_proxy",
        "policy_passed", "action_type", "parameter_value", "entity_context",
    ]

    def test_all_required_fields_present(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        for field in self.REQUIRED_FIELDS:
            assert field in card, f"Missing field: {field}"

    def test_card_id_has_dc_prefix(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        assert card["card_id"].startswith("dc_")

    def test_action_card_id_preserved(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        assert card["action_card_id"] == "act_test_001"

    def test_run_id_preserved(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_abc"
        )
        assert card["run_id"] == "run_abc"

    def test_policy_decision_preserved_action_type(self, playbook):
        card = build_single_decision_card(
            _make_action_card(action_type="NO_ACTION", parameter_value=0.0, policy_passed=False),
            "Product", playbook, "run_test"
        )
        assert card["action_type"] == "NO_ACTION"
        assert card["parameter_value"] == 0.0
        assert card["policy_passed"] is False

    def test_json_serialisable(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        # Should not raise
        json.dumps(card)

    def test_evidence_chain_is_list(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        assert isinstance(card["evidence_chain"], list)
        assert len(card["evidence_chain"]) > 0

    def test_checks_to_run_is_list(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        assert isinstance(card["checks_to_run"], list)

    def test_entity_context_has_expected_keys(self, playbook):
        card = build_single_decision_card(
            _make_action_card(product_id="P999", customer_id="C888"),
            "Product", playbook, "run_test"
        )
        ec = card["entity_context"]
        assert ec["product_id"] == "P999"
        assert ec["customer_id"] == "C888"
        assert ec["replenishment_source"] == "Product"


# ---------------------------------------------------------------------------
# B continued — confidence / validation_status per tier
# ---------------------------------------------------------------------------

class TestCardConfidencePerTier:
    def test_product_tier_card_is_high_recommendation(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        assert card["confidence"] == "high"
        assert card["validation_status"] == "RECOMMENDATION"

    def test_aisle_tier_card_is_medium_recommendation(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Aisle", playbook, "run_test"
        )
        assert card["confidence"] == "medium"
        assert card["validation_status"] == "RECOMMENDATION"

    def test_department_tier_card_is_low_hypothesis(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Department", playbook, "run_test"
        )
        assert card["confidence"] == "low"
        assert card["validation_status"] == "HYPOTHESIS"

    def test_global_default_tier_card_is_low_hypothesis(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Global_Default", playbook, "run_test"
        )
        assert card["confidence"] == "low"
        assert card["validation_status"] == "HYPOTHESIS"


# ---------------------------------------------------------------------------
# C. Evidence chain annotation
# ---------------------------------------------------------------------------

class TestEvidenceChainAnnotation:
    def test_step_1_has_overdue_ratio(self, playbook):
        card = build_single_decision_card(
            _make_action_card(overdue_ratio=1.6), "Product", playbook, "run_test"
        )
        step1 = next(s for s in card["evidence_chain"] if s["step"] == 1)
        assert step1["observed_value"] == 1.6

    def test_step_2_contains_replenishment_source_tier(self, playbook):
        card = build_single_decision_card(
            _make_action_card(overdue_ratio=1.6), "Aisle", playbook, "run_test"
        )
        step2 = next(s for s in card["evidence_chain"] if s["step"] == 2)
        assert "Aisle" in str(step2["observed_value"])

    def test_step_3_has_inventory_level(self, playbook):
        card = build_single_decision_card(
            _make_action_card(inventory_level=80), "Product", playbook, "run_test"
        )
        step3 = next(s for s in card["evidence_chain"] if s["step"] == 3)
        assert step3["observed_value"] == 80

    def test_step_4_has_gross_margin_pct(self, playbook):
        card = build_single_decision_card(
            _make_action_card(gross_margin_pct=0.25), "Product", playbook, "run_test"
        )
        step4 = next(s for s in card["evidence_chain"] if s["step"] == 4)
        assert step4["observed_value"] == 0.25

    def test_each_step_has_observation_and_implication(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        for step in card["evidence_chain"]:
            assert "observation" in step
            assert "implication" in step


# ---------------------------------------------------------------------------
# D. Diagnosis summary
# ---------------------------------------------------------------------------

class TestDiagnosisSummary:
    def test_recommendation_card_contains_overdue_pct(self, playbook):
        card = build_single_decision_card(
            _make_action_card(overdue_ratio=1.5), "Product", playbook, "run_test"
        )
        # 1.5 → 50% overdue
        assert "50%" in card["diagnosis_summary"]

    def test_recommendation_card_contains_customer_and_product(self, playbook):
        card = build_single_decision_card(
            _make_action_card(customer_id="C777", product_id="P444"),
            "Product", playbook, "run_test"
        )
        assert "C777" in card["diagnosis_summary"]
        assert "P444" in card["diagnosis_summary"]

    def test_hypothesis_card_diagnosis_mentions_hypothesis(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Department", playbook, "run_test"
        )
        assert "HYPOTHESIS" in card["diagnosis_summary"]

    def test_hypothesis_card_diagnosis_mentions_source_tier(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Department", playbook, "run_test"
        )
        assert "Department" in card["diagnosis_summary"]


# ---------------------------------------------------------------------------
# E. Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_recommendation_policy_passed_has_discount_first(self, playbook):
        card = build_single_decision_card(
            _make_action_card(policy_passed=True, action_type="DISCOUNT", parameter_value=0.15),
            "Product", playbook, "run_test"
        )
        recs = card["recommendations"]
        assert recs[0]["action_type"] == "DISCOUNT"
        assert recs[0]["priority"] == 1

    def test_recommendation_policy_passed_discount_has_parameter(self, playbook):
        card = build_single_decision_card(
            _make_action_card(policy_passed=True, action_type="DISCOUNT", parameter_value=0.10),
            "Product", playbook, "run_test"
        )
        assert "10%" in card["recommendations"][0]["recommendation"]

    def test_recommendation_policy_passed_discount_is_semi_automated(self, playbook):
        card = build_single_decision_card(
            _make_action_card(policy_passed=True, action_type="DISCOUNT"),
            "Product", playbook, "run_test"
        )
        assert card["recommendations"][0]["automation_level"] == "semi_automated"

    def test_policy_blocked_no_discount_in_recs(self, playbook):
        card = build_single_decision_card(
            _make_action_card(policy_passed=False, action_type="NO_ACTION"),
            "Product", playbook, "run_test"
        )
        action_types = [r["action_type"] for r in card["recommendations"]]
        assert "DISCOUNT" not in action_types

    def test_hypothesis_all_recs_are_contingent(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Department", playbook, "run_test"
        )
        for rec in card["recommendations"]:
            assert "[CONTINGENT ON CHECKS]" in rec["recommendation"]

    def test_hypothesis_all_recs_are_warn_only(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Department", playbook, "run_test"
        )
        for rec in card["recommendations"]:
            assert rec["automation_level"] == "warn_only"

    def test_recommendations_sorted_by_priority(self, playbook):
        card = build_single_decision_card(
            _make_action_card(policy_passed=True), "Product", playbook, "run_test"
        )
        priorities = [r["priority"] for r in card["recommendations"]]
        assert priorities == sorted(priorities)


# ---------------------------------------------------------------------------
# F. Likely causes
# ---------------------------------------------------------------------------

class TestLikelyCauses:
    def test_recommendation_card_causes_not_prefixed(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        for cause in card["likely_causes"]:
            assert "[HYPOTHESIS]" not in cause["cause"]

    def test_hypothesis_card_causes_are_prefixed(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Global_Default", playbook, "run_test"
        )
        for cause in card["likely_causes"]:
            assert cause["cause"].startswith("[HYPOTHESIS]")

    def test_hypothesis_card_causes_have_low_confidence(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Global_Default", playbook, "run_test"
        )
        for cause in card["likely_causes"]:
            assert cause["confidence"] == "low"

    def test_recommendation_card_causes_preserve_original_confidence(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        # Playbook first cause is "medium" confidence
        assert card["likely_causes"][0]["confidence"] == "medium"


# ---------------------------------------------------------------------------
# G. risk_notes
# ---------------------------------------------------------------------------

class TestRiskNotes:
    def test_recommendation_card_has_empty_risk_notes(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Product", playbook, "run_test"
        )
        assert card["risk_notes"] == []

    def test_hypothesis_card_has_risk_note(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Department", playbook, "run_test"
        )
        assert len(card["risk_notes"]) > 0
        assert "Department" in card["risk_notes"][0]

    def test_hypothesis_risk_note_mentions_validate(self, playbook):
        card = build_single_decision_card(
            _make_action_card(), "Global_Default", playbook, "run_test"
        )
        assert "validate" in card["risk_notes"][0].lower()


# ---------------------------------------------------------------------------
# H. Evidence as JSON string (CSV serialisation compat)
# ---------------------------------------------------------------------------

class TestEvidenceAsJsonString:
    def test_evidence_string_parsed_correctly(self, playbook):
        card = build_single_decision_card(
            _make_action_card(evidence_as_string=True, product_id="P555"),
            "Aisle", playbook, "run_test"
        )
        assert card["entity_context"]["product_id"] == "P555"

    def test_evidence_string_confidence_derived(self, playbook):
        card = build_single_decision_card(
            _make_action_card(evidence_as_string=True),
            "Aisle", playbook, "run_test"
        )
        assert card["confidence"] == "medium"
        assert card["validation_status"] == "RECOMMENDATION"


# ---------------------------------------------------------------------------
# I. build_decision_cards — file-level integration
# ---------------------------------------------------------------------------

def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class TestBuildDecisionCards:
    def _setup_fixtures(self, tmp_path: Path):
        """Write minimal action_cards_v0.csv and products_canonical.csv."""
        ac_path = str(tmp_path / "action_cards_v0.csv")
        prod_path = str(tmp_path / "products_canonical.csv")
        out_path = str(tmp_path / "decision_cards.jsonl")

        evidence_high = json.dumps({
            "customer_id": "C001", "product_id": "P001",
            "overdue_ratio": 1.5, "inventory_level": 100,
            "gross_margin_pct": 0.30, "rfm_segment": "Champions",
        })
        evidence_hypo = json.dumps({
            "customer_id": "C002", "product_id": "P002",
            "overdue_ratio": 1.3, "inventory_level": 80,
            "gross_margin_pct": 0.25, "rfm_segment": "At Risk",
        })
        evidence_blocked = json.dumps({
            "customer_id": "C003", "product_id": "P003",
            "overdue_ratio": 1.8, "inventory_level": 20,
            "gross_margin_pct": 0.10, "rfm_segment": "Loyal",
        })

        action_cards = [
            {"id": "ac1", "action_type": "DISCOUNT", "parameter_value": 0.15,
             "policy_passed": True,  "evidence": evidence_high},
            {"id": "ac2", "action_type": "DISCOUNT", "parameter_value": 0.15,
             "policy_passed": True,  "evidence": evidence_hypo},
            {"id": "ac3", "action_type": "NO_ACTION", "parameter_value": 0.0,
             "policy_passed": False, "evidence": evidence_blocked},
        ]
        _write_csv(ac_path, action_cards,
                   ["id", "action_type", "parameter_value", "policy_passed", "evidence"])

        products = [
            {"product_id": "P001", "replenishment_source": "Product"},
            {"product_id": "P002", "replenishment_source": "Department"},
            {"product_id": "P003", "replenishment_source": "Aisle"},
        ]
        _write_csv(prod_path, products, ["product_id", "replenishment_source"])

        return ac_path, prod_path, out_path

    def test_returns_list_of_dicts(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_integration")
        assert isinstance(cards, list)
        assert all(isinstance(c, dict) for c in cards)

    def test_correct_number_of_cards_written(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_integration")
        assert len(cards) == 3

    def test_output_file_is_valid_jsonl(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        build_decision_cards(ac, prod, out, run_id="run_integration")
        with open(out) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 3

    def test_replenishment_source_lookup_applied(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_integration")
        p001_card = next(c for c in cards if c["entity_context"]["product_id"] == "P001")
        assert p001_card["confidence"] == "high"
        assert p001_card["validation_status"] == "RECOMMENDATION"

    def test_hypothesis_card_created_for_department_tier(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_integration")
        p002_card = next(c for c in cards if c["entity_context"]["product_id"] == "P002")
        assert p002_card["validation_status"] == "HYPOTHESIS"

    def test_policy_blocked_card_has_no_discount_rec(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_integration")
        p003_card = next(c for c in cards if c["entity_context"]["product_id"] == "P003")
        action_types = [r["action_type"] for r in p003_card["recommendations"]]
        assert "DISCOUNT" not in action_types

    def test_max_cards_limits_output(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_integration", max_cards=1)
        assert len(cards) == 1

    def test_unknown_product_gets_global_default(self, tmp_path, playbook):
        ac_path = str(tmp_path / "action_cards_orphan.csv")
        prod_path = str(tmp_path / "products_canonical.csv")
        out_path = str(tmp_path / "decision_cards_orphan.jsonl")

        evidence = json.dumps({
            "customer_id": "C999", "product_id": "P_UNKNOWN",
            "overdue_ratio": 1.5, "inventory_level": 60,
        })
        _write_csv(ac_path,
                   [{"id": "ac_orphan", "action_type": "DISCOUNT",
                     "parameter_value": 0.10, "policy_passed": True, "evidence": evidence}],
                   ["id", "action_type", "parameter_value", "policy_passed", "evidence"])
        _write_csv(prod_path, [{"product_id": "P001", "replenishment_source": "Product"}],
                   ["product_id", "replenishment_source"])

        cards = build_decision_cards(ac_path, prod_path, out_path, run_id="run_orphan")
        assert cards[0]["validation_status"] == "HYPOTHESIS"
        assert cards[0]["entity_context"]["replenishment_source"] == "Global_Default"

    def test_run_id_propagated_to_all_cards(self, tmp_path, playbook):
        ac, prod, out = self._setup_fixtures(tmp_path)
        cards = build_decision_cards(ac, prod, out, run_id="run_XYZ_123")
        assert all(c["run_id"] == "run_XYZ_123" for c in cards)
