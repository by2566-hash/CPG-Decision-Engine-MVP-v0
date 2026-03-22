"""
tests/test_llm_renderer_decision_card.py

Tests for the decision-card-aware LLM renderer and Gate E validator.

Coverage:
  A. generate_merchant_explanation — pure function, all fields, HYPOTHESIS vs RECOMMENDATION
  B. generate_mock_llm_response — with/without decision_card, inject_error compat
  C. Gate E — pattern mismatch, missing field, missing uncertainty_note for HYPOTHESIS
  D. process_action_card — merchant_explanation pass-through, absent on fallback
  E. Backward compatibility — existing tests still pass with new optional params
"""
from __future__ import annotations

import json

import pytest

from llm_explainability_renderer import generate_merchant_explanation, generate_mock_llm_response
from llm_response_hydrator import process_action_card
from llm_safety_gateway import ErrorCodes, LLMSafetyGateway

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def validator():
    return LLMSafetyGateway()


@pytest.fixture
def base_action_card():
    return {
        "id": "act_001",
        "action_type": "DISCOUNT",
        "parameter_value": 0.15,
        "policy_passed": True,
        "evidence": {
            "customer_id": "C001",
            "product_id": "P001",
            "rfm_segment": "Champions",
            "overdue_ratio": 1.5,
            "inventory_level": 100,
        },
    }


def _make_decision_card(
    *,
    validation_status: str = "RECOMMENDATION",
    pattern: str = "replenishment_overdue_at_risk_customer",
    diagnosis_summary: str = "Customer C001 is 50% overdue for P001 (overdue_ratio=1.50).",
    evidence_chain=None,
    checks_to_run=None,
    recommendations=None,
    risk_notes=None,
    action_card_id: str = "act_001",
):
    if evidence_chain is None:
        evidence_chain = [
            {"step": 1, "observation": "obs1", "implication": "imp1", "data_source": "ds1", "observed_value": 1.5},
            {"step": 2, "observation": "obs2", "implication": "imp2", "data_source": "ds2", "observed_value": "1.50x (cycle from Product tier)"},
            {"step": 3, "observation": "obs3", "implication": "imp3", "data_source": "ds3", "observed_value": 100},
            {"step": 4, "observation": "obs4", "implication": "imp4", "data_source": "ds4", "observed_value": 0.32},
        ]
    if checks_to_run is None:
        checks_to_run = [
            {"check_id": "check_rfm_segment", "description": "...", "why": "...", "confirm_if": "...", "data_source": "..."},
            {"check_id": "check_overdue_magnitude", "description": "...", "why": "...", "confirm_if": "...", "data_source": "..."},
        ]
    if recommendations is None:
        recommendations = [
            {"priority": 1, "action_type": "DISCOUNT",
             "recommendation": "Offer 15% discount.", "automation_level": "semi_automated",
             "preconditions": ["policy_passed == True"]},
        ]
    if risk_notes is None:
        risk_notes = (
            ["Replenishment cycle derived from Global_Default-tier data. Treat as hypothesis."]
            if validation_status == "HYPOTHESIS" else []
        )
    return {
        "card_id": "dc_test",
        "action_card_id": action_card_id,
        "rule_id": "rule_replenishment_discount_v1",
        "run_id": "run_test",
        "generated_at": "2026-03-19T00:00:00+00:00",
        "pattern_detected": pattern,
        "diagnosis_summary": diagnosis_summary,
        "evidence_chain": evidence_chain,
        "checks_to_run": checks_to_run,
        "recommendations": recommendations,
        "do_not_recommend": [],
        "why": "Re-engaging at-risk customer.",
        "why_not": [],
        "risk_notes": risk_notes,
        "confidence": "low" if validation_status == "HYPOTHESIS" else "high",
        "validation_status": validation_status,
        "expected_impact_proxy": {"description": "...", "key_metrics": [], "estimated_improvement": None},
        "policy_passed": True,
        "action_type": "DISCOUNT",
        "parameter_value": 0.15,
        "entity_context": {
            "customer_id": "C001", "product_id": "P001", "rfm_segment": "Champions",
            "overdue_ratio": 1.5, "inventory_level": 100, "replenishment_source": "Product",
        },
    }


# ---------------------------------------------------------------------------
# A. generate_merchant_explanation — pure function
# ---------------------------------------------------------------------------

class TestGenerateMerchantExplanation:
    def test_required_fields_present(self):
        dc = _make_decision_card()
        me = generate_merchant_explanation(dc)
        for field in ["pattern", "summary", "key_evidence", "top_recommendation",
                      "checks_required", "uncertainty_note"]:
            assert field in me, f"Missing: {field}"

    def test_pattern_matches_pattern_detected(self):
        dc = _make_decision_card(pattern="replenishment_overdue_at_risk_customer")
        me = generate_merchant_explanation(dc)
        assert me["pattern"] == "replenishment_overdue_at_risk_customer"

    def test_summary_is_verbatim_diagnosis_summary(self):
        summary = "Customer C001 is 50% overdue."
        dc = _make_decision_card(diagnosis_summary=summary)
        me = generate_merchant_explanation(dc)
        assert me["summary"] == summary

    def test_key_evidence_populated_from_chain(self):
        dc = _make_decision_card()
        me = generate_merchant_explanation(dc)
        assert len(me["key_evidence"]) > 0

    def test_key_evidence_contains_observed_values(self):
        dc = _make_decision_card()
        me = generate_merchant_explanation(dc)
        # step 1 observed_value is 1.5
        assert any("1.5" in str(ev) for ev in me["key_evidence"])

    def test_key_evidence_skips_steps_without_observed_value(self):
        dc = _make_decision_card(evidence_chain=[
            {"step": 1, "observation": "o", "implication": "i",
             "data_source": "d"},  # no observed_value
            {"step": 2, "observation": "o", "implication": "i",
             "data_source": "d", "observed_value": 99},
        ])
        me = generate_merchant_explanation(dc)
        assert len(me["key_evidence"]) == 1
        assert "Step 2" in me["key_evidence"][0]

    def test_top_recommendation_from_first_rec(self):
        dc = _make_decision_card()
        me = generate_merchant_explanation(dc)
        assert me["top_recommendation"] == "Offer 15% discount."

    def test_top_recommendation_fallback_when_no_recs(self):
        dc = _make_decision_card(recommendations=[])
        me = generate_merchant_explanation(dc)
        assert me["top_recommendation"] == "No action recommended."

    def test_recommendation_card_checks_required_is_empty(self):
        dc = _make_decision_card(validation_status="RECOMMENDATION")
        me = generate_merchant_explanation(dc)
        assert me["checks_required"] == []

    def test_hypothesis_card_checks_required_populated(self):
        dc = _make_decision_card(validation_status="HYPOTHESIS")
        me = generate_merchant_explanation(dc)
        assert "check_rfm_segment" in me["checks_required"]
        assert "check_overdue_magnitude" in me["checks_required"]

    def test_recommendation_card_uncertainty_note_is_none(self):
        dc = _make_decision_card(validation_status="RECOMMENDATION")
        me = generate_merchant_explanation(dc)
        assert me["uncertainty_note"] is None

    def test_hypothesis_card_uncertainty_note_present(self):
        dc = _make_decision_card(validation_status="HYPOTHESIS")
        me = generate_merchant_explanation(dc)
        assert me["uncertainty_note"] is not None
        assert len(me["uncertainty_note"]) > 0

    def test_hypothesis_uncertainty_note_from_risk_notes(self):
        note = "Cycle from Global_Default-tier. Validate before acting."
        dc = _make_decision_card(validation_status="HYPOTHESIS", risk_notes=[note])
        me = generate_merchant_explanation(dc)
        assert me["uncertainty_note"] == note

    def test_hypothesis_no_risk_notes_uncertainty_is_none(self):
        dc = _make_decision_card(validation_status="HYPOTHESIS", risk_notes=[])
        me = generate_merchant_explanation(dc)
        assert me["uncertainty_note"] is None


# ---------------------------------------------------------------------------
# B. generate_mock_llm_response — with/without decision_card
# ---------------------------------------------------------------------------

class TestGenerateMockLlmResponse:
    def test_without_decision_card_no_merchant_explanation(self, base_action_card):
        resp = json.loads(generate_mock_llm_response(base_action_card))
        assert "merchant_explanation" not in resp

    def test_with_decision_card_includes_merchant_explanation(self, base_action_card):
        dc = _make_decision_card()
        resp = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        assert "merchant_explanation" in resp

    def test_merchant_explanation_pattern_matches(self, base_action_card):
        dc = _make_decision_card(pattern="replenishment_overdue_at_risk_customer")
        resp = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        assert resp["merchant_explanation"]["pattern"] == "replenishment_overdue_at_risk_customer"

    def test_with_decision_card_copy_still_present(self, base_action_card):
        dc = _make_decision_card()
        resp = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        assert "copy" in resp
        assert "body_template" in resp["copy"]

    def test_inject_error_still_works_with_decision_card(self, base_action_card):
        dc = _make_decision_card()
        resp = json.loads(generate_mock_llm_response(
            base_action_card, inject_error="hallucinate_discount", decision_card=dc
        ))
        assert resp["policy_echo"]["discount_pct"] == 0.99

    def test_policy_echo_not_modified_by_decision_card(self, base_action_card):
        dc = _make_decision_card()
        resp = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        assert resp["policy_echo"]["discount_pct"] == 0.15
        assert resp["policy_echo"]["action_type"] == "DISCOUNT"

    def test_response_is_json_serialisable(self, base_action_card):
        dc = _make_decision_card()
        raw = generate_mock_llm_response(base_action_card, decision_card=dc)
        parsed = json.loads(raw)
        json.dumps(parsed)  # should not raise

    def test_rfm_segment_used_in_grounding(self, base_action_card):
        resp = json.loads(generate_mock_llm_response(base_action_card))
        fields = [f["field"] for f in resp["grounding"]["facts_used"]]
        assert "rfm_segment" in fields


# ---------------------------------------------------------------------------
# C. Gate E — merchant_explanation validation
# ---------------------------------------------------------------------------

class TestGateE:
    def test_passes_when_no_merchant_explanation(self, validator, base_action_card):
        dc = _make_decision_card()
        llm_resp = generate_mock_llm_response(base_action_card)  # no decision_card → no ME field
        errors = validator.validate(llm_resp, base_action_card, decision_card=dc)
        assert not any(e.startswith("E_MERCHANT") for e in errors)

    def test_passes_valid_recommendation_explanation(self, validator, base_action_card):
        dc = _make_decision_card(validation_status="RECOMMENDATION")
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        errors = validator.validate(llm_resp, base_action_card, decision_card=dc)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_PATTERN_MISMATCH not in errors
        assert ErrorCodes.E_MERCHANT_EXPLANATION_MISSING_FIELD not in errors
        assert ErrorCodes.E_MERCHANT_EXPLANATION_MISSING_UNCERTAINTY not in errors

    def test_passes_valid_hypothesis_explanation(self, validator, base_action_card):
        dc = _make_decision_card(validation_status="HYPOTHESIS")
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        errors = validator.validate(llm_resp, base_action_card, decision_card=dc)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_MISSING_UNCERTAINTY not in errors

    def test_gate_e_pattern_mismatch(self, validator, base_action_card):
        dc = _make_decision_card(pattern="replenishment_overdue_at_risk_customer")
        raw = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        raw["merchant_explanation"]["pattern"] = "completely_wrong_pattern"
        errors = validator.validate(json.dumps(raw), base_action_card, decision_card=dc)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_PATTERN_MISMATCH in errors

    def test_gate_e_missing_required_field(self, validator, base_action_card):
        dc = _make_decision_card()
        raw = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        del raw["merchant_explanation"]["summary"]
        errors = validator.validate(json.dumps(raw), base_action_card, decision_card=dc)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_MISSING_FIELD in errors

    def test_gate_e_hypothesis_missing_uncertainty_note(self, validator, base_action_card):
        dc = _make_decision_card(validation_status="HYPOTHESIS")
        raw = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        raw["merchant_explanation"]["uncertainty_note"] = None
        errors = validator.validate(json.dumps(raw), base_action_card, decision_card=dc)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_MISSING_UNCERTAINTY in errors

    def test_gate_e_not_triggered_without_decision_card(self, validator, base_action_card):
        dc = _make_decision_card()
        raw = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        raw["merchant_explanation"]["pattern"] = "wrong"
        # No decision_card passed to validate → Gate E skipped
        errors = validator.validate(json.dumps(raw), base_action_card)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_PATTERN_MISMATCH not in errors

    def test_gate_e_recommendation_card_no_uncertainty_required(self, validator, base_action_card):
        dc = _make_decision_card(validation_status="RECOMMENDATION")
        raw = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        raw["merchant_explanation"]["uncertainty_note"] = None
        errors = validator.validate(json.dumps(raw), base_action_card, decision_card=dc)
        assert ErrorCodes.E_MERCHANT_EXPLANATION_MISSING_UNCERTAINTY not in errors

    def test_existing_gates_unaffected_by_decision_card_param(self, validator, base_action_card):
        dc = _make_decision_card()
        raw = json.loads(generate_mock_llm_response(base_action_card, decision_card=dc))
        raw["policy_echo"]["discount_pct"] = 0.99  # trigger Gate B
        errors = validator.validate(json.dumps(raw), base_action_card, decision_card=dc)
        assert ErrorCodes.E_POLICY_ECHO_DISCOUNT_MISMATCH in errors


# ---------------------------------------------------------------------------
# D. process_action_card — merchant_explanation in output
# ---------------------------------------------------------------------------

class TestProcessActionCardMerchantExplanation:
    def test_merchant_explanation_included_on_pass(self, validator, base_action_card):
        dc = _make_decision_card()
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        result = process_action_card(base_action_card, llm_resp, validator, decision_card=dc)
        assert result["validation_status"] == "PASS"
        assert "merchant_explanation" in result

    def test_merchant_explanation_has_expected_fields(self, validator, base_action_card):
        dc = _make_decision_card()
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        result = process_action_card(base_action_card, llm_resp, validator, decision_card=dc)
        me = result["merchant_explanation"]
        assert "pattern" in me
        assert "summary" in me
        assert "key_evidence" in me
        assert "top_recommendation" in me

    def test_merchant_explanation_absent_on_fallback(self, validator, base_action_card):
        dc = _make_decision_card()
        bad_resp = generate_mock_llm_response(base_action_card, inject_error="hallucinate_discount", decision_card=dc)
        result = process_action_card(base_action_card, bad_resp, validator, decision_card=dc)
        assert result["validation_status"] == "FALLBACK"
        assert "merchant_explanation" not in result

    def test_merchant_explanation_absent_when_not_in_response(self, validator, base_action_card):
        llm_resp = generate_mock_llm_response(base_action_card)  # no decision_card
        result = process_action_card(base_action_card, llm_resp, validator)
        assert "merchant_explanation" not in result

    def test_policy_echo_unchanged_in_output(self, validator, base_action_card):
        dc = _make_decision_card()
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        result = process_action_card(base_action_card, llm_resp, validator, decision_card=dc)
        assert result["policy_echo"]["action_type"] == "DISCOUNT"
        assert result["policy_echo"]["discount_pct"] == 0.15

    def test_hypothesis_explanation_has_uncertainty_note(self, validator, base_action_card):
        dc = _make_decision_card(validation_status="HYPOTHESIS")
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        result = process_action_card(base_action_card, llm_resp, validator, decision_card=dc)
        assert result["validation_status"] == "PASS"
        me = result["merchant_explanation"]
        assert me["uncertainty_note"] is not None

    def test_hypothesis_explanation_has_checks_required(self, validator, base_action_card):
        dc = _make_decision_card(validation_status="HYPOTHESIS")
        llm_resp = generate_mock_llm_response(base_action_card, decision_card=dc)
        result = process_action_card(base_action_card, llm_resp, validator, decision_card=dc)
        me = result["merchant_explanation"]
        assert len(me["checks_required"]) > 0


# ---------------------------------------------------------------------------
# E. Backward compatibility — no regressions
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_validate_two_args_still_works(self, validator):
        """Existing callers that don't pass decision_card must continue to work."""
        card = {
            "id": "act_compat",
            "action_type": "DISCOUNT",
            "parameter_value": 0.10,
            "evidence": {"product_id": "P99", "rfm_segment": "Loyal"},
        }
        resp = {
            "action_trade_id": "act_compat",
            "policy_echo": {"action_type": "DISCOUNT", "discount_pct": 0.10},
            "grounding": {"facts_used": [{"field": "product_id", "value": "P99"}]},
            "copy": {"channel": "sms", "body_template": "Your {PRODUCT} is ready. {DISCOUNT_TEXT} off!"},
        }
        errors = validator.validate(json.dumps(resp), card)
        assert errors == []

    def test_process_action_card_two_args_still_works(self, validator):
        card = {
            "id": "act_compat2",
            "action_type": "DISCOUNT",
            "parameter_value": 0.10,
            "evidence": {"product_id": "P88", "rfm_segment": "Loyal"},
        }
        llm_resp = generate_mock_llm_response(card)
        result = process_action_card(card, llm_resp, validator)
        assert "action_trade_id" in result
        assert "final_llm_copy" in result

    def test_generate_mock_llm_response_no_decision_card(self):
        card = {
            "id": "act_compat3",
            "action_type": "NO_ACTION",
            "parameter_value": 0.0,
            "evidence": {"product_id": "P77", "rfm_segment": "At Risk"},
        }
        raw = generate_mock_llm_response(card)
        resp = json.loads(raw)
        assert "merchant_explanation" not in resp
        assert "copy" in resp
