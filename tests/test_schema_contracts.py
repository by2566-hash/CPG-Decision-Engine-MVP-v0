"""
tests/test_schema_contracts.py

Validates the v1 schema contracts and example playbooks against their JSON Schemas.

Covers:
  - Playbook schema structure (required fields, enums, nested objects)
  - Both playbook YAML files validate against playbook_schema_v1.json
  - Merchant decision card schema structure
  - Valid and invalid merchant decision card examples
  - Action card schema v1 backward compatibility with v0 cards
  - Action card schema v1 new fields
  - do_not_recommend / common_ai_failure_modes field coverage
  - validation_status enum
  - confidence enum
"""

import json
import os
import copy
import pytest
import yaml
from jsonschema import validate, ValidationError, Draft202012Validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(rel_path: str) -> dict:
    with open(os.path.join(ROOT, rel_path), "r") as f:
        return json.load(f)


def load_yaml(rel_path: str) -> dict:
    with open(os.path.join(ROOT, rel_path), "r") as f:
        return yaml.safe_load(f)


def assert_valid(instance, schema):
    """Asserts that instance validates against schema. Raises on failure with a clear message."""
    try:
        validate(instance=instance, schema=schema)
    except ValidationError as e:
        pytest.fail(f"Unexpected validation failure: {e.message} at path {list(e.absolute_path)}")


def assert_invalid(instance, schema, *, expected_path=None):
    """Asserts that instance fails validation against schema."""
    with pytest.raises(ValidationError) as exc_info:
        validate(instance=instance, schema=schema)
    if expected_path is not None:
        actual_path = list(exc_info.value.absolute_path)
        assert expected_path in actual_path or str(expected_path) in [str(p) for p in actual_path], (
            f"Expected error at path containing {expected_path!r}, got {actual_path}"
        )


# ---------------------------------------------------------------------------
# Schema fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def playbook_schema():
    return load_json("CONTRACTS/playbook_schema_v1.json")


@pytest.fixture(scope="module")
def merchant_card_schema():
    return load_json("CONTRACTS/merchant_decision_card_schema_v1.json")


@pytest.fixture(scope="module")
def action_card_schema():
    return load_json("CONTRACTS/action_card_schema_v1.json")


# ---------------------------------------------------------------------------
# Minimal valid object factories
# ---------------------------------------------------------------------------

def minimal_playbook() -> dict:
    """Returns the smallest object that satisfies playbook_schema_v1."""
    return {
        "rule_id": "rule_test_minimal_v1",
        "version": "1.0",
        "module": {
            "primary": "customer_retention",
            "domain": "customer_lifecycle",
            "business_model": "cpg_dtc"
        },
        "context": {
            "merchant_type": "CPG DTC brand"
        },
        "problem_pattern": {
            "name": "test_pattern",
            "business_risk": ["lost revenue"]
        },
        "trigger": {
            "conditions": [
                {"metric": "overdue_ratio", "op": ">=", "value": 1.2}
            ],
            "description": "Fire when overdue_ratio >= 1.2"
        },
        "diagnostic_logic": {
            "evidence_chain": [
                {
                    "step": 1,
                    "observation": "Customer has not repurchased within the expected cycle.",
                    "implication": "Customer is at risk of churning."
                }
            ]
        },
        "diagnosis_summary": "Customer is overdue for replenishment.",
        "likely_causes": [
            {"cause": "Passive timing drift", "confidence": "medium"}
        ],
        "checks_to_run": [
            {
                "check_id": "check_rfm_segment",
                "description": "Confirm RFM segment.",
                "why": "Discount should target high-value customers."
            }
        ],
        "suggested_actions": [
            {
                "action_type": "DISCOUNT",
                "priority": 1,
                "recommendation": "Offer 15% off.",
                "automation_level": "semi_automated"
            }
        ],
        "expected_impact_proxy": {
            "description": "Re-engage at-risk customer within 30 days."
        },
        "do_not_recommend": [],
        "common_ai_failure_modes": [],
        "confidence": "medium",
        "validation_status": "RECOMMENDATION",
        "action_lever": "replenishment_discount_nudge"
    }


def minimal_merchant_card() -> dict:
    """Returns the smallest object that satisfies merchant_decision_card_schema_v1."""
    return {
        "card_id": "card_abc123",
        "rule_id": "rule_test_minimal_v1",
        "run_id": "run_20240101T120000_abc123",
        "generated_at": "2024-01-01T12:00:00Z",
        "pattern_detected": "Replenishment overdue for high-value customers.",
        "diagnosis_summary": "Customer is 30% overdue for replenishment of their top product.",
        "evidence_chain": [
            {
                "step": 1,
                "observation": "overdue_ratio = 1.45 vs expected cycle of 21 days.",
                "implication": "Customer is materially overdue."
            }
        ],
        "checks_to_run": [
            {
                "check_id": "check_rfm_segment",
                "description": "Confirm RFM segment.",
                "why": "Discount should target high-value customers."
            }
        ],
        "recommendations": [
            {
                "priority": 1,
                "action_type": "DISCOUNT",
                "recommendation": "Offer 15% off replenishment.",
                "automation_level": "semi_automated"
            }
        ],
        "why": "The customer is overdue by 30% and has historically repurchased reliably.",
        "confidence": "medium",
        "expected_impact_proxy": {
            "description": "Expected to recover one purchase cycle within 30 days."
        },
        "validation_status": "RECOMMENDATION"
    }


def minimal_v0_action_card() -> dict:
    """Mimics a real v0 action card as emitted by policy_engine.py."""
    return {
        "id": "act_a1b2c3d4",
        "run_id": "run_v0_001",
        "policy_version": "v0_baseline",
        "action_type": "DISCOUNT",
        "parameter_value": 0.15,
        "policy_passed": True,
        "expected_value_proxy": 12.34,
        "evidence": json.dumps({
            "customer_id": "42",
            "product_id": "999",
            "rfm_segment": "Loyal",
            "overdue_ratio": 1.45,
            "inventory_level": 120,
            "gross_margin_pct": 0.38
        }),
        "constraints_applied": json.dumps([
            {"rule": "max_actions_per_user", "value": 1, "check": "PASS"},
            {"rule": "inventory_floor", "value": 50, "check": "PASS"},
            {"rule": "margin_floor", "value": 0.15, "check": "PASS"}
        ]),
        "llm_explanation": "Since your product replenishment is 45% overdue, we are offering a 15% discount."
    }


# ===========================================================================
# SECTION 1 — PLAYBOOK SCHEMA
# ===========================================================================

class TestPlaybookSchemaStructure:
    """Validates structural rules of the playbook schema itself."""

    def test_schema_loads_as_valid_json(self, playbook_schema):
        assert "$schema" in playbook_schema
        assert playbook_schema["type"] == "object"

    def test_schema_has_all_required_top_level_fields(self, playbook_schema):
        required = set(playbook_schema["required"])
        expected = {
            "rule_id", "version", "module", "context", "problem_pattern",
            "trigger", "diagnostic_logic", "diagnosis_summary", "likely_causes",
            "checks_to_run", "suggested_actions", "expected_impact_proxy",
            "do_not_recommend", "common_ai_failure_modes",
            "confidence", "validation_status", "action_lever"
        }
        assert expected.issubset(required), f"Missing required fields: {expected - required}"

    def test_confidence_enum_values(self, playbook_schema):
        enum_vals = playbook_schema["properties"]["confidence"]["enum"]
        assert set(enum_vals) == {"high", "medium", "low"}

    def test_validation_status_enum_values(self, playbook_schema):
        enum_vals = playbook_schema["properties"]["validation_status"]["enum"]
        assert set(enum_vals) == {"RECOMMENDATION", "HYPOTHESIS", "INSUFFICIENT_DATA"}

    def test_automation_level_enum_in_suggested_actions(self, playbook_schema):
        items = playbook_schema["properties"]["suggested_actions"]["items"]
        enum_vals = items["properties"]["automation_level"]["enum"]
        assert set(enum_vals) == {"automated", "semi_automated", "warn_only", "operator_only"}

    def test_evidence_chain_step_minimum_is_1(self, playbook_schema):
        ec_items = (
            playbook_schema["properties"]["diagnostic_logic"]
            ["properties"]["evidence_chain"]["items"]
        )
        assert ec_items["properties"]["step"]["minimum"] == 1

    def test_rule_id_pattern_enforced(self, playbook_schema):
        assert "pattern" in playbook_schema["properties"]["rule_id"]


class TestPlaybookMinimalValid:
    """The minimal valid playbook should pass all required field checks."""

    def test_minimal_playbook_passes(self, playbook_schema):
        assert_valid(minimal_playbook(), playbook_schema)

    def test_missing_rule_id_fails(self, playbook_schema):
        p = minimal_playbook()
        del p["rule_id"]
        assert_invalid(p, playbook_schema)

    def test_missing_module_fails(self, playbook_schema):
        p = minimal_playbook()
        del p["module"]
        assert_invalid(p, playbook_schema)

    def test_missing_diagnostic_logic_fails(self, playbook_schema):
        p = minimal_playbook()
        del p["diagnostic_logic"]
        assert_invalid(p, playbook_schema)

    def test_missing_do_not_recommend_fails(self, playbook_schema):
        p = minimal_playbook()
        del p["do_not_recommend"]
        assert_invalid(p, playbook_schema)

    def test_missing_common_ai_failure_modes_fails(self, playbook_schema):
        p = minimal_playbook()
        del p["common_ai_failure_modes"]
        assert_invalid(p, playbook_schema)

    def test_missing_action_lever_fails(self, playbook_schema):
        p = minimal_playbook()
        del p["action_lever"]
        assert_invalid(p, playbook_schema)

    def test_invalid_rule_id_pattern_fails(self, playbook_schema):
        p = minimal_playbook()
        p["rule_id"] = "BadRuleId"  # must start with rule_
        assert_invalid(p, playbook_schema)

    def test_invalid_confidence_enum_fails(self, playbook_schema):
        p = minimal_playbook()
        p["confidence"] = "very_high"
        assert_invalid(p, playbook_schema)

    def test_invalid_validation_status_enum_fails(self, playbook_schema):
        p = minimal_playbook()
        p["validation_status"] = "MAYBE"
        assert_invalid(p, playbook_schema)

    def test_invalid_automation_level_fails(self, playbook_schema):
        p = minimal_playbook()
        p["suggested_actions"][0]["automation_level"] = "fully_autonomous"
        assert_invalid(p, playbook_schema)

    def test_evidence_chain_empty_array_fails(self, playbook_schema):
        p = minimal_playbook()
        p["diagnostic_logic"]["evidence_chain"] = []
        assert_invalid(p, playbook_schema)

    def test_evidence_chain_missing_observation_fails(self, playbook_schema):
        p = minimal_playbook()
        p["diagnostic_logic"]["evidence_chain"][0].pop("observation")
        assert_invalid(p, playbook_schema)

    def test_likely_causes_empty_fails(self, playbook_schema):
        p = minimal_playbook()
        p["likely_causes"] = []
        assert_invalid(p, playbook_schema)

    def test_likely_cause_invalid_confidence_fails(self, playbook_schema):
        p = minimal_playbook()
        p["likely_causes"][0]["confidence"] = "uncertain"
        assert_invalid(p, playbook_schema)

    def test_do_not_recommend_item_missing_reason_fails(self, playbook_schema):
        p = minimal_playbook()
        p["do_not_recommend"] = [{"action_type": "DISCOUNT"}]  # missing reason
        assert_invalid(p, playbook_schema)

    def test_extra_top_level_field_fails(self, playbook_schema):
        p = minimal_playbook()
        p["undocumented_field"] = "oops"
        assert_invalid(p, playbook_schema)


class TestPlaybookWithOptionalFields:
    """Playbooks with valid optional fields should also pass."""

    def test_playbook_with_do_not_recommend_entries(self, playbook_schema):
        p = minimal_playbook()
        p["do_not_recommend"] = [
            {"action_type": "TURN_OFF_AD_SET_COMPLETELY", "reason": "Historical performance was strong."}
        ]
        assert_valid(p, playbook_schema)

    def test_playbook_with_common_ai_failure_modes(self, playbook_schema):
        p = minimal_playbook()
        p["common_ai_failure_modes"] = [
            {"failure_mode": "Blaming creative fatigue without checking CVR.", "guard": "Verify upper-funnel stability first."}
        ]
        assert_valid(p, playbook_schema)

    def test_playbook_hypothesis_status(self, playbook_schema):
        p = minimal_playbook()
        p["validation_status"] = "HYPOTHESIS"
        p["confidence"] = "low"
        assert_valid(p, playbook_schema)

    def test_playbook_with_required_data_sources(self, playbook_schema):
        p = minimal_playbook()
        p["diagnostic_logic"]["required_data_sources"] = {
            "minimum_viable": ["meta_ads", "ga4"],
            "recommended": ["google_ads"]
        }
        assert_valid(p, playbook_schema)

    def test_playbook_with_full_module(self, playbook_schema):
        p = minimal_playbook()
        p["module"]["secondary"] = "conversion_merchandising"
        p["module"]["vertical"] = "food_beverage_coffee"
        p["module"]["channels"] = ["meta_ads", "ga4"]
        p["module"]["entities"] = ["campaign", "ad_set", "landing_page"]
        assert_valid(p, playbook_schema)

    def test_playbook_with_expected_impact_key_metrics(self, playbook_schema):
        p = minimal_playbook()
        p["expected_impact_proxy"]["key_metrics"] = ["cac_7d", "mobile_cvr"]
        p["expected_impact_proxy"]["estimated_improvement"] = "-15% CAC over 30 days"
        assert_valid(p, playbook_schema)


# ===========================================================================
# SECTION 2 — PLAYBOOK YAML FILES
# ===========================================================================

class TestPlaybookYamlFiles:
    """Both YAML playbook files must validate against playbook_schema_v1."""

    def test_replenishment_playbook_is_valid(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        assert_valid(playbook, playbook_schema)

    def test_cac_spike_playbook_is_valid(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        assert_valid(playbook, playbook_schema)

    def test_replenishment_playbook_rule_id(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        assert playbook["rule_id"] == "rule_replenishment_discount_v1"

    def test_cac_spike_playbook_rule_id(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        assert playbook["rule_id"] == "rule_cac_spike_meta_lp_mobile_v1"

    def test_replenishment_playbook_has_do_not_recommend(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        assert isinstance(playbook["do_not_recommend"], list)
        assert len(playbook["do_not_recommend"]) >= 1
        for entry in playbook["do_not_recommend"]:
            assert "action_type" in entry
            assert "reason" in entry

    def test_cac_spike_playbook_has_do_not_recommend(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        forbidden = [d["action_type"] for d in playbook["do_not_recommend"]]
        assert "TURN_OFF_AD_SET_COMPLETELY" in forbidden
        assert "REFRESH_CREATIVE" in forbidden

    def test_replenishment_playbook_has_common_ai_failure_modes(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        modes = playbook["common_ai_failure_modes"]
        assert isinstance(modes, list)
        assert len(modes) >= 1

    def test_cac_spike_playbook_has_common_ai_failure_modes(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        modes = playbook["common_ai_failure_modes"]
        assert isinstance(modes, list)
        assert len(modes) >= 2

    def test_replenishment_playbook_evidence_chain_ordered(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        chain = playbook["diagnostic_logic"]["evidence_chain"]
        steps = [e["step"] for e in chain]
        assert steps == sorted(steps), "Evidence chain steps must be in ascending order."

    def test_cac_spike_playbook_evidence_chain_ordered(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        chain = playbook["diagnostic_logic"]["evidence_chain"]
        steps = [e["step"] for e in chain]
        assert steps == sorted(steps)

    def test_cac_spike_playbook_high_confidence(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        assert playbook["confidence"] == "high"

    def test_replenishment_playbook_medium_confidence(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        assert playbook["confidence"] == "medium"

    def test_cac_spike_suggested_actions_have_priorities(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        priorities = sorted([a["priority"] for a in playbook["suggested_actions"]])
        assert priorities[0] == 1, "Highest priority action must be priority 1."

    def test_replenishment_playbook_action_lever_set(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        assert playbook["action_lever"] == "replenishment_discount_nudge"

    def test_cac_spike_playbook_action_lever_set(self, playbook_schema):
        playbook = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        assert playbook["action_lever"] == "ad_spend_reduction_plus_lp_fix"


# ===========================================================================
# SECTION 3 — MERCHANT DECISION CARD SCHEMA
# ===========================================================================

class TestMerchantCardSchemaStructure:
    """Validates structural rules of the merchant decision card schema."""

    def test_schema_loads(self, merchant_card_schema):
        assert merchant_card_schema["type"] == "object"

    def test_required_fields_present(self, merchant_card_schema):
        required = set(merchant_card_schema["required"])
        expected = {
            "card_id", "rule_id", "run_id", "generated_at",
            "pattern_detected", "diagnosis_summary",
            "evidence_chain", "checks_to_run", "recommendations",
            "why", "confidence", "expected_impact_proxy", "validation_status"
        }
        assert expected.issubset(required), f"Missing required fields: {expected - required}"

    def test_confidence_enum(self, merchant_card_schema):
        enum_vals = merchant_card_schema["properties"]["confidence"]["enum"]
        assert set(enum_vals) == {"high", "medium", "low"}

    def test_validation_status_enum(self, merchant_card_schema):
        enum_vals = merchant_card_schema["properties"]["validation_status"]["enum"]
        assert set(enum_vals) == {"RECOMMENDATION", "HYPOTHESIS", "INSUFFICIENT_DATA"}


class TestMerchantCardMinimalValid:
    def test_minimal_card_passes(self, merchant_card_schema):
        assert_valid(minimal_merchant_card(), merchant_card_schema)

    def test_missing_card_id_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["card_id"]
        assert_invalid(c, merchant_card_schema)

    def test_missing_rule_id_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["rule_id"]
        assert_invalid(c, merchant_card_schema)

    def test_missing_pattern_detected_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["pattern_detected"]
        assert_invalid(c, merchant_card_schema)

    def test_missing_diagnosis_summary_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["diagnosis_summary"]
        assert_invalid(c, merchant_card_schema)

    def test_missing_evidence_chain_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["evidence_chain"]
        assert_invalid(c, merchant_card_schema)

    def test_missing_why_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["why"]
        assert_invalid(c, merchant_card_schema)

    def test_missing_expected_impact_proxy_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        del c["expected_impact_proxy"]
        assert_invalid(c, merchant_card_schema)

    def test_invalid_confidence_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["confidence"] = "very_high"
        assert_invalid(c, merchant_card_schema)

    def test_invalid_validation_status_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["validation_status"] = "PENDING"
        assert_invalid(c, merchant_card_schema)

    def test_invalid_automation_level_in_recommendations_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["recommendations"][0]["automation_level"] = "magic"
        assert_invalid(c, merchant_card_schema)

    def test_empty_evidence_chain_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["evidence_chain"] = []
        assert_invalid(c, merchant_card_schema)

    def test_empty_recommendations_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["recommendations"] = []
        assert_invalid(c, merchant_card_schema)

    def test_invalid_rule_id_pattern_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["rule_id"] = "BadId"
        assert_invalid(c, merchant_card_schema)

    def test_extra_field_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["unexpected_field"] = "oops"
        assert_invalid(c, merchant_card_schema)


class TestMerchantCardOptionalFields:
    def test_card_with_why_not(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["why_not"] = [
            {"action_type": "TURN_OFF_AD_SET_COMPLETELY", "reason": "Historical performance was strong."}
        ]
        assert_valid(c, merchant_card_schema)

    def test_card_with_risk_notes(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["risk_notes"] = [
            "Sufficient sample size required for CVR diagnosis.",
            "Do not over-infer from sparse device-model data."
        ]
        assert_valid(c, merchant_card_schema)

    def test_card_with_merchant_id(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["merchant_id"] = "merchant_globalink_01"
        assert_valid(c, merchant_card_schema)

    def test_card_with_observed_values_in_evidence_chain(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["evidence_chain"][0]["observed_value"] = 1.45
        c["evidence_chain"][0]["data_source"] = "ga4"
        assert_valid(c, merchant_card_schema)

    def test_hypothesis_card_is_valid(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["validation_status"] = "HYPOTHESIS"
        c["confidence"] = "low"
        assert_valid(c, merchant_card_schema)

    def test_insufficient_data_card_is_valid(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["validation_status"] = "INSUFFICIENT_DATA"
        c["confidence"] = "low"
        assert_valid(c, merchant_card_schema)

    def test_card_with_full_expected_impact_proxy(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["expected_impact_proxy"]["key_metrics"] = ["cac_7d", "mobile_cvr"]
        c["expected_impact_proxy"]["estimated_improvement"] = "-15% CAC over 30 days"
        assert_valid(c, merchant_card_schema)

    def test_check_with_status(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["checks_to_run"][0]["status"] = "confirmed"
        assert_valid(c, merchant_card_schema)

    def test_check_with_invalid_status_fails(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["checks_to_run"][0]["status"] = "unknown_status"
        assert_invalid(c, merchant_card_schema)

    def test_multiple_prioritized_recommendations(self, merchant_card_schema):
        c = minimal_merchant_card()
        c["recommendations"] = [
            {"priority": 1, "action_type": "REDUCE_SPEND_ON_AFFECTED_AD_SET",
             "recommendation": "Reduce spend by 50%.", "automation_level": "semi_automated"},
            {"priority": 2, "action_type": "VALIDATE_PAGE_ON_AFFECTED_DEVICE_ENVIRONMENTS",
             "recommendation": "Test on Android/WebView.", "automation_level": "warn_only"},
        ]
        assert_valid(c, merchant_card_schema)


# ===========================================================================
# SECTION 4 — ACTION CARD SCHEMA v1 (backward compat + new fields)
# ===========================================================================

class TestActionCardSchemaV1BackwardCompat:
    """v0 action cards (as emitted by policy_engine.py) must still pass v1 schema."""

    def test_v0_action_card_passes_v1_schema(self, action_card_schema):
        assert_valid(minimal_v0_action_card(), action_card_schema)

    def test_v0_no_action_card_passes_v1_schema(self, action_card_schema):
        card = minimal_v0_action_card()
        card["action_type"] = "NO_ACTION"
        card["parameter_value"] = 0.0
        card["policy_passed"] = False
        assert_valid(card, action_card_schema)

    def test_v0_card_without_expected_value_proxy_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        del card["expected_value_proxy"]
        assert_valid(card, action_card_schema)

    def test_v0_card_without_llm_explanation_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        del card["llm_explanation"]
        assert_valid(card, action_card_schema)

    def test_action_type_invalid_enum_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        card["action_type"] = "MAGIC_ACTION"
        assert_invalid(card, action_card_schema)

    def test_missing_id_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        del card["id"]
        assert_invalid(card, action_card_schema)

    def test_missing_policy_passed_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        del card["policy_passed"]
        assert_invalid(card, action_card_schema)

    def test_parameter_value_above_1_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        card["parameter_value"] = 1.5
        assert_invalid(card, action_card_schema)


class TestActionCardSchemaV1NewFields:
    """New v1 fields are optional but must validate correctly when present."""

    def test_v1_card_with_rule_id_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["rule_id"] = "rule_replenishment_discount_v1"
        assert_valid(card, action_card_schema)

    def test_v1_card_with_invalid_rule_id_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        card["rule_id"] = "BadRuleId"
        assert_invalid(card, action_card_schema)

    def test_v1_card_with_confidence_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["confidence"] = "high"
        assert_valid(card, action_card_schema)

    def test_v1_card_with_invalid_confidence_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        card["confidence"] = "uncertain"
        assert_invalid(card, action_card_schema)

    def test_v1_card_with_validation_status_recommendation(self, action_card_schema):
        card = minimal_v0_action_card()
        card["validation_status"] = "RECOMMENDATION"
        assert_valid(card, action_card_schema)

    def test_v1_card_with_validation_status_hypothesis(self, action_card_schema):
        card = minimal_v0_action_card()
        card["validation_status"] = "HYPOTHESIS"
        assert_valid(card, action_card_schema)

    def test_v1_card_with_automation_level_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["automation_level"] = "semi_automated"
        assert_valid(card, action_card_schema)

    def test_v1_card_with_invalid_automation_level_fails(self, action_card_schema):
        card = minimal_v0_action_card()
        card["automation_level"] = "fully_autonomous"
        assert_invalid(card, action_card_schema)

    def test_v1_card_with_diagnostic_logic_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["diagnostic_logic"] = {
            "pattern_detected": "replenishment_overdue_at_risk_customer",
            "diagnosis_summary": "Customer is 45% overdue for product 999.",
            "evidence_chain": [
                {
                    "step": 1,
                    "observation": "overdue_ratio = 1.45",
                    "implication": "Customer is at replenishment risk.",
                    "data_source": "compute_risk.py",
                    "observed_value": 1.45
                }
            ],
            "checks_completed": [
                {"check_id": "check_rfm_segment", "status": "confirmed"}
            ]
        }
        assert_valid(card, action_card_schema)

    def test_v1_card_with_do_not_recommend_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["do_not_recommend"] = ["TURN_OFF_PRODUCT_LISTING", "FULL_REFUND_OFFER"]
        assert_valid(card, action_card_schema)

    def test_v1_card_with_expected_impact_proxy_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["expected_impact_proxy"] = {
            "description": "Recover one replenishment cycle within 30 days.",
            "key_metrics": ["repurchase_rate_within_30d"],
            "estimated_improvement": "+20pp repurchase rate"
        }
        assert_valid(card, action_card_schema)

    def test_v1_card_with_priority_passes(self, action_card_schema):
        card = minimal_v0_action_card()
        card["priority"] = 1
        assert_valid(card, action_card_schema)

    def test_v1_full_enriched_card(self, action_card_schema):
        """A fully populated v1 card with all optional fields should pass."""
        card = minimal_v0_action_card()
        card.update({
            "rule_id": "rule_replenishment_discount_v1",
            "priority": 1,
            "automation_level": "semi_automated",
            "confidence": "medium",
            "validation_status": "RECOMMENDATION",
            "diagnostic_logic": {
                "pattern_detected": "replenishment_overdue_at_risk_customer",
                "diagnosis_summary": "Customer 42 is 45% overdue for product 999.",
                "evidence_chain": [
                    {"step": 1, "observation": "overdue_ratio = 1.45",
                     "implication": "Churn risk is elevated.", "data_source": "compute_risk.py"}
                ],
                "checks_completed": [
                    {"check_id": "check_rfm_segment", "status": "confirmed"},
                    {"check_id": "check_margin_viability", "status": "confirmed"}
                ]
            },
            "do_not_recommend": ["TURN_OFF_PRODUCT_LISTING"],
            "expected_impact_proxy": {
                "description": "Re-engage at-risk customer within 30 days.",
                "key_metrics": ["repurchase_rate_within_30d", "contribution_margin_per_action"],
                "estimated_improvement": "+20pp repurchase within one cycle"
            }
        })
        assert_valid(card, action_card_schema)


# ===========================================================================
# SECTION 5 — CROSS-SCHEMA CONSISTENCY
# ===========================================================================

class TestCrossSchemaConsistency:
    """Checks consistency between the three schemas and the playbook files."""

    def test_playbook_rule_ids_match_expected_pattern(self):
        replenishment = load_yaml("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        cac_spike = load_yaml("PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml")
        import re
        pattern = re.compile(r"^rule_[a-z0-9_]+$")
        assert pattern.match(replenishment["rule_id"])
        assert pattern.match(cac_spike["rule_id"])

    def test_suggested_action_types_not_in_do_not_recommend(self):
        """No playbook should recommend an action it also forbids."""
        for path in [
            "PLAYBOOKS/rule_replenishment_discount_v1.yaml",
            "PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml"
        ]:
            playbook = load_yaml(path)
            suggested = {a["action_type"] for a in playbook["suggested_actions"]}
            forbidden = {d["action_type"] for d in playbook["do_not_recommend"]}
            overlap = suggested & forbidden
            assert not overlap, (
                f"{path}: action types appear in both suggested_actions and do_not_recommend: {overlap}"
            )

    def test_evidence_chain_steps_are_unique_and_sequential(self):
        for path in [
            "PLAYBOOKS/rule_replenishment_discount_v1.yaml",
            "PLAYBOOKS/rule_cac_spike_meta_lp_mobile_v1.yaml"
        ]:
            playbook = load_yaml(path)
            chain = playbook["diagnostic_logic"]["evidence_chain"]
            steps = [e["step"] for e in chain]
            assert len(steps) == len(set(steps)), f"{path}: duplicate step numbers in evidence_chain"
            assert steps == list(range(1, len(steps) + 1)), (
                f"{path}: evidence_chain steps must be consecutive from 1, got {steps}"
            )

    def test_merchant_card_rule_id_must_match_playbook_pattern(self, merchant_card_schema):
        """A merchant card referencing a real playbook rule_id should pass schema validation."""
        c = minimal_merchant_card()
        for rule_id in [
            "rule_replenishment_discount_v1",
            "rule_cac_spike_meta_lp_mobile_v1"
        ]:
            c["rule_id"] = rule_id
            assert_valid(c, merchant_card_schema)

    def test_existing_llm_render_schema_is_unchanged(self):
        """The original llm_render_output_schema.json must still be loadable and unmodified."""
        schema = load_json("CONTRACTS/llm_render_output_schema.json")
        assert schema["title"] == "LLM Render Output Schema"
        assert "action_trade_id" in schema["required"]
        assert "policy_echo" in schema["required"]
        assert "grounding" in schema["required"]
        assert "copy" in schema["required"]
