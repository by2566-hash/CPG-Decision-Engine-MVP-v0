"""
tests/test_playbook_engine.py

Tests for the playbook_engine package.

Sections:
  1. TestPlaybookModels     — from_dict, computed properties
  2. TestPlaybookLoader     — load YAML/JSON, validation, directory loading
  3. TestTriggerEvaluation  — condition-level matching logic
  4. TestPlaybookEvaluator  — full evaluation + output structure
  5. TestIntegration        — real playbook files end-to-end
"""

import json
import os
import tempfile

import pytest
import yaml

from playbook_engine import (
    PlaybookEvaluator,
    PlaybookLoader,
    PlaybookValidationError,
)
from playbook_engine.models import (
    Context,
    DiagnosticLogic,
    EvidenceStep,
    Module,
    Playbook,
    SuggestedAction,
    Trigger,
    TriggerCondition,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYBOOKS_DIR = os.path.join(ROOT, "PLAYBOOKS")
SCHEMA_PATH = os.path.join(ROOT, "CONTRACTS", "playbook_schema_v1.json")
REPLENISHMENT_YAML = os.path.join(PLAYBOOKS_DIR, "rule_replenishment_discount_v1.yaml")
CAC_SPIKE_YAML = os.path.join(PLAYBOOKS_DIR, "rule_cac_spike_meta_lp_mobile_v1.yaml")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loader():
    return PlaybookLoader(schema_path=SCHEMA_PATH)


@pytest.fixture(scope="module")
def evaluator():
    return PlaybookEvaluator()


@pytest.fixture(scope="module")
def replenishment_playbook(loader):
    return loader.load(REPLENISHMENT_YAML)


@pytest.fixture(scope="module")
def cac_spike_playbook(loader):
    return loader.load(CAC_SPIKE_YAML)


@pytest.fixture(scope="module")
def registry(loader):
    return loader.load_directory(PLAYBOOKS_DIR)


# ---------------------------------------------------------------------------
# Factory helpers — build minimal valid raw dicts in memory for unit tests
# ---------------------------------------------------------------------------

def _minimal_raw_playbook(**overrides) -> dict:
    """Returns the smallest raw dict that satisfies the playbook schema."""
    base = {
        "rule_id": "rule_test_unit_v1",
        "version": "1.0",
        "module": {"primary": "customer_retention", "domain": "customer_lifecycle",
                   "business_model": "cpg_dtc"},
        "context": {"merchant_type": "CPG DTC brand"},
        "problem_pattern": {"name": "test_pattern", "business_risk": ["lost revenue"]},
        "trigger": {
            "conditions": [{"metric": "overdue_ratio", "op": ">=", "value": 1.2}],
            "description": "Fire when overdue_ratio >= 1.2",
        },
        "diagnostic_logic": {
            "evidence_chain": [
                {"step": 1,
                 "observation": "Overdue.",
                 "implication": "At risk."}
            ]
        },
        "diagnosis_summary": "Customer is overdue.",
        "likely_causes": [{"cause": "Forgot to repurchase", "confidence": "medium"}],
        "checks_to_run": [
            {"check_id": "check_rfm", "description": "Check RFM.", "why": "Segment matters."}
        ],
        "suggested_actions": [
            {"action_type": "DISCOUNT", "priority": 1,
             "recommendation": "Offer 15% off.", "automation_level": "semi_automated"}
        ],
        "expected_impact_proxy": {"description": "Re-engage within 30 days."},
        "do_not_recommend": [],
        "common_ai_failure_modes": [],
        "confidence": "medium",
        "validation_status": "RECOMMENDATION",
        "action_lever": "replenishment_discount_nudge",
    }
    base.update(overrides)
    return base


def _playbook_from_minimal(**overrides) -> Playbook:
    return Playbook.from_dict(_minimal_raw_playbook(**overrides))


# ===========================================================================
# 1. PLAYBOOK MODELS
# ===========================================================================

class TestPlaybookModels:

    # ---- TriggerCondition ----

    def test_trigger_condition_from_dict_numeric_value(self):
        tc = TriggerCondition.from_dict({"metric": "overdue_ratio", "op": ">=", "value": 1.2})
        assert tc.metric == "overdue_ratio"
        assert tc.op == ">="
        assert tc.value == 1.2
        assert tc.relative_to is None

    def test_trigger_condition_from_dict_string_value(self):
        """value can be a metric-name reference."""
        tc = TriggerCondition.from_dict(
            {"metric": "spend_growth_rate", "op": ">", "value": "customer_growth_rate"}
        )
        assert isinstance(tc.value, str)
        assert tc.value == "customer_growth_rate"

    def test_trigger_condition_relative_to(self):
        tc = TriggerCondition.from_dict(
            {"metric": "cac_7d", "op": ">=", "value": 1.15,
             "relative_to": "prior_7d_same_dow_baseline"}
        )
        assert tc.relative_to == "prior_7d_same_dow_baseline"

    # ---- EvidenceStep ----

    def test_evidence_step_all_fields(self):
        es = EvidenceStep.from_dict({
            "step": 2,
            "observation": "CVR dropped -17% on mobile.",
            "implication": "Technical issue on mobile.",
            "data_source": "ga4",
        })
        assert es.step == 2
        assert es.data_source == "ga4"

    def test_evidence_step_optional_data_source(self):
        es = EvidenceStep.from_dict(
            {"step": 1, "observation": "Obs.", "implication": "Imp."}
        )
        assert es.data_source is None

    # ---- SuggestedAction ----

    def test_suggested_action_with_preconditions(self):
        sa = SuggestedAction.from_dict({
            "action_type": "DISCOUNT",
            "priority": 1,
            "recommendation": "Offer 15% off.",
            "automation_level": "semi_automated",
            "preconditions": ["overdue_ratio >= 1.2", "inventory > 50"],
        })
        assert len(sa.preconditions) == 2

    def test_suggested_action_no_preconditions(self):
        sa = SuggestedAction.from_dict({
            "action_type": "DISCOUNT", "priority": 1,
            "recommendation": "r", "automation_level": "warn_only",
        })
        assert sa.preconditions == []

    # ---- Module / Context defaults ----

    def test_module_optional_lists_default_to_empty(self):
        m = Module.from_dict(
            {"primary": "retention", "domain": "lifecycle", "business_model": "cpg"}
        )
        assert m.channels == []
        assert m.entities == []
        assert m.secondary is None

    def test_context_optional_lists_default_to_empty(self):
        c = Context.from_dict({"merchant_type": "CPG DTC"})
        assert c.applies_broadly_to == []
        assert c.products_in_scope == []

    # ---- Playbook.from_dict ----

    def test_playbook_from_dict_builds_correctly(self):
        pb = _playbook_from_minimal()
        assert pb.rule_id == "rule_test_unit_v1"
        assert pb.version == "1.0"
        assert isinstance(pb.trigger, Trigger)
        assert isinstance(pb.diagnostic_logic, DiagnosticLogic)
        assert len(pb.suggested_actions) == 1

    def test_playbook_from_dict_sorts_suggested_actions_by_priority(self):
        raw = _minimal_raw_playbook()
        raw["suggested_actions"] = [
            {"action_type": "REMINDER", "priority": 2,
             "recommendation": "r2", "automation_level": "warn_only"},
            {"action_type": "DISCOUNT", "priority": 1,
             "recommendation": "r1", "automation_level": "semi_automated"},
        ]
        pb = Playbook.from_dict(raw)
        assert pb.suggested_actions[0].priority == 1
        assert pb.suggested_actions[1].priority == 2

    def test_playbook_version_coerced_to_string(self):
        """YAML may parse '1.0' as float 1.0 — from_dict must coerce to str."""
        raw = _minimal_raw_playbook()
        raw["version"] = 1.0   # simulate YAML float
        pb = Playbook.from_dict(raw)
        assert isinstance(pb.version, str)

    # ---- Computed properties ----

    def test_forbidden_actions_returns_frozenset(self):
        raw = _minimal_raw_playbook()
        raw["do_not_recommend"] = [
            {"action_type": "TURN_OFF_PRODUCT_LISTING", "reason": "Disproportionate."},
            {"action_type": "FULL_REFUND_OFFER", "reason": "Not warranted."},
        ]
        pb = Playbook.from_dict(raw)
        assert "TURN_OFF_PRODUCT_LISTING" in pb.forbidden_actions
        assert "FULL_REFUND_OFFER" in pb.forbidden_actions
        assert "DISCOUNT" not in pb.forbidden_actions

    def test_safe_suggested_actions_excludes_forbidden(self):
        raw = _minimal_raw_playbook()
        raw["suggested_actions"] = [
            {"action_type": "DISCOUNT", "priority": 1,
             "recommendation": "r", "automation_level": "semi_automated"},
            {"action_type": "TURN_OFF_PRODUCT_LISTING", "priority": 2,
             "recommendation": "bad", "automation_level": "operator_only"},
        ]
        raw["do_not_recommend"] = [
            {"action_type": "TURN_OFF_PRODUCT_LISTING", "reason": "Disproportionate."}
        ]
        pb = Playbook.from_dict(raw)
        safe = pb.safe_suggested_actions
        types = [a.action_type for a in safe]
        assert "DISCOUNT" in types
        assert "TURN_OFF_PRODUCT_LISTING" not in types

    def test_safe_suggested_actions_all_safe_returns_all(self):
        pb = _playbook_from_minimal()
        assert len(pb.safe_suggested_actions) == len(pb.suggested_actions)

    def test_minimum_viable_sources_from_diagnostic_logic(self):
        raw = _minimal_raw_playbook()
        raw["diagnostic_logic"]["required_data_sources"] = {
            "minimum_viable": ["meta_ads", "ga4"],
            "recommended": ["google_ads"],
        }
        pb = Playbook.from_dict(raw)
        assert pb.minimum_viable_sources == ["meta_ads", "ga4"]

    def test_minimum_viable_sources_empty_when_absent(self):
        pb = _playbook_from_minimal()
        assert pb.minimum_viable_sources == []


# ===========================================================================
# 2. PLAYBOOK LOADER
# ===========================================================================

class TestPlaybookLoader:

    def test_load_replenishment_yaml(self, loader):
        pb = loader.load(REPLENISHMENT_YAML)
        assert isinstance(pb, Playbook)
        assert pb.rule_id == "rule_replenishment_discount_v1"

    def test_load_cac_spike_yaml(self, loader):
        pb = loader.load(CAC_SPIKE_YAML)
        assert isinstance(pb, Playbook)
        assert pb.rule_id == "rule_cac_spike_meta_lp_mobile_v1"

    def test_load_json_file(self, loader):
        """A valid playbook serialised to JSON must load correctly."""
        raw = _minimal_raw_playbook()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(raw, f)
            tmp_path = f.name
        try:
            pb = loader.load(tmp_path)
            assert pb.rule_id == "rule_test_unit_v1"
        finally:
            os.unlink(tmp_path)

    def test_load_yml_extension(self, loader):
        """Both .yaml and .yml must be accepted."""
        raw = _minimal_raw_playbook()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump(raw, f)
            tmp_path = f.name
        try:
            pb = loader.load(tmp_path)
            assert isinstance(pb, Playbook)
        finally:
            os.unlink(tmp_path)

    def test_unsupported_extension_raises_value_error(self, loader):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as f:
            f.write("rule_id = 'x'\n")
            tmp_path = f.name
        try:
            with pytest.raises(ValueError, match="Unsupported file format"):
                loader.load(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_missing_file_raises_file_not_found(self, loader):
        with pytest.raises(FileNotFoundError):
            loader.load("/no/such/path/playbook.yaml")

    def test_invalid_data_raises_playbook_validation_error(self, loader):
        raw = {"rule_id": "bad_id"}   # missing required fields, bad pattern
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            tmp_path = f.name
        try:
            with pytest.raises(PlaybookValidationError) as exc_info:
                loader.load(tmp_path)
            assert exc_info.value.path == tmp_path
            assert len(exc_info.value.errors) > 0
        finally:
            os.unlink(tmp_path)

    def test_playbook_validation_error_has_readable_message(self, loader):
        raw = {"rule_id": "bad", "version": "1.0"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            tmp_path = f.name
        try:
            with pytest.raises(PlaybookValidationError) as exc_info:
                loader.load(tmp_path)
            msg = str(exc_info.value)
            assert "validation failed" in msg.lower()
        finally:
            os.unlink(tmp_path)

    def test_validate_raw_returns_empty_for_valid(self, loader):
        raw = _minimal_raw_playbook()
        errors = loader.validate_raw(raw)
        assert errors == []

    def test_validate_raw_returns_errors_for_invalid(self, loader):
        errors = loader.validate_raw({"rule_id": "bad_no_prefix"})
        assert len(errors) > 0

    def test_validate_raw_catches_bad_confidence_enum(self, loader):
        raw = _minimal_raw_playbook()
        raw["confidence"] = "extremely_high"
        errors = loader.validate_raw(raw)
        assert any("confidence" in e or "extremely_high" in e for e in errors)

    def test_load_directory_returns_dict_keyed_by_rule_id(self, loader):
        registry = loader.load_directory(PLAYBOOKS_DIR)
        assert "rule_replenishment_discount_v1" in registry
        assert "rule_cac_spike_meta_lp_mobile_v1" in registry

    def test_load_directory_returns_playbook_objects(self, loader):
        registry = loader.load_directory(PLAYBOOKS_DIR)
        for pb in registry.values():
            assert isinstance(pb, Playbook)

    def test_load_directory_nonexistent_raises(self, loader):
        with pytest.raises(FileNotFoundError):
            loader.load_directory("/no/such/playbooks/dir")

    def test_load_directory_skips_invalid_file_without_crashing(self, loader):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write one valid playbook
            valid_path = os.path.join(tmpdir, "rule_valid_test_v1.yaml")
            with open(valid_path, "w") as f:
                raw = _minimal_raw_playbook()
                raw["rule_id"] = "rule_valid_test_v1"
                yaml.dump(raw, f)
            # Write one invalid playbook
            invalid_path = os.path.join(tmpdir, "rule_bad.yaml")
            with open(invalid_path, "w") as f:
                yaml.dump({"rule_id": "broken"}, f)
            # Must load the valid one and skip the invalid
            registry = loader.load_directory(tmpdir)
            assert "rule_valid_test_v1" in registry
            assert "rule_bad" not in registry


# ===========================================================================
# 3. TRIGGER CONDITION EVALUATION
# ===========================================================================

class TestTriggerEvaluation:
    """Tests for the per-condition evaluation logic inside PlaybookEvaluator."""

    # Convenience: exercise _eval_condition through the public evaluate() path.
    def _get_trigger_results(self, evaluator, pb, metrics):
        result = evaluator.evaluate(pb, metrics)
        return result.trigger_results

    def test_single_condition_matches(self, evaluator):
        pb = _playbook_from_minimal()  # condition: overdue_ratio >= 1.2
        results = self._get_trigger_results(evaluator, pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        assert results[0].matched is True
        assert results[0].skipped is False

    def test_single_condition_does_not_match(self, evaluator):
        pb = _playbook_from_minimal()
        results = self._get_trigger_results(evaluator, pb, {"overdue_ratio": 0.9, "mock_inventory": 100})
        assert results[0].matched is False

    def test_missing_metric_is_skipped_not_crashed(self, evaluator):
        pb = _playbook_from_minimal()
        results = self._get_trigger_results(evaluator, pb, {})   # no metrics at all
        assert results[0].skipped is True
        assert results[0].matched is False
        assert results[0].skip_reason is not None

    def test_skipped_condition_reason_mentions_metric_name(self, evaluator):
        pb = _playbook_from_minimal()
        results = self._get_trigger_results(evaluator, pb, {})
        assert "overdue_ratio" in results[0].skip_reason

    def test_boundary_equals_threshold_matches_ge(self, evaluator):
        pb = _playbook_from_minimal()   # >= 1.2
        results = self._get_trigger_results(evaluator, pb, {"overdue_ratio": 1.2, "mock_inventory": 100})
        assert results[0].matched is True

    def test_boundary_just_below_threshold_fails_ge(self, evaluator):
        pb = _playbook_from_minimal()
        results = self._get_trigger_results(evaluator, pb, {"overdue_ratio": 1.199, "mock_inventory": 100})
        assert results[0].matched is False

    def test_all_operators(self, evaluator):
        """Smoke-test each supported operator."""
        op_cases = [
            (">=", 5, 5, True),
            (">=", 4, 5, False),
            (">",  6, 5, True),
            (">",  5, 5, False),
            ("<=", 5, 5, True),
            ("<=", 6, 5, False),
            ("<",  4, 5, True),
            ("<",  5, 5, False),
            ("==", 5, 5, True),
            ("==", 4, 5, False),
            ("!=", 4, 5, True),
            ("!=", 5, 5, False),
        ]
        for op, observed_val, threshold, expected_match in op_cases:
            raw = _minimal_raw_playbook()
            raw["trigger"]["conditions"] = [
                {"metric": "x", "op": op, "value": threshold}
            ]
            pb = Playbook.from_dict(raw)
            results = self._get_trigger_results(evaluator, pb, {"x": observed_val})
            assert results[0].matched is expected_match, (
                f"Failed: {observed_val} {op} {threshold} expected {expected_match}"
            )

    def test_unsupported_operator_is_skipped(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["trigger"]["conditions"] = [{"metric": "x", "op": "???", "value": 1}]
        pb = Playbook.from_dict(raw)
        results = self._get_trigger_results(evaluator, pb, {"x": 5})
        assert results[0].skipped is True
        assert "unsupported operator" in results[0].skip_reason

    def test_metric_reference_resolves_and_matches(self, evaluator):
        """value='customer_growth_rate' should resolve against the metrics dict."""
        raw = _minimal_raw_playbook()
        raw["trigger"]["conditions"] = [
            {"metric": "spend_growth_rate", "op": ">", "value": "customer_growth_rate"}
        ]
        pb = Playbook.from_dict(raw)
        results = self._get_trigger_results(
            evaluator, pb,
            {"spend_growth_rate": 0.8, "customer_growth_rate": 0.4}
        )
        assert results[0].matched is True
        assert results[0].threshold == 0.4   # resolved value

    def test_metric_reference_fails_when_target_exceeds_ref(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["trigger"]["conditions"] = [
            {"metric": "spend_growth_rate", "op": ">", "value": "customer_growth_rate"}
        ]
        pb = Playbook.from_dict(raw)
        results = self._get_trigger_results(
            evaluator, pb,
            {"spend_growth_rate": 0.3, "customer_growth_rate": 0.4}
        )
        assert results[0].matched is False

    def test_reference_metric_missing_is_skipped(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["trigger"]["conditions"] = [
            {"metric": "spend_growth_rate", "op": ">", "value": "customer_growth_rate"}
        ]
        pb = Playbook.from_dict(raw)
        # customer_growth_rate is absent
        results = self._get_trigger_results(evaluator, pb, {"spend_growth_rate": 0.8})
        assert results[0].skipped is True
        assert "customer_growth_rate" in results[0].skip_reason

    def test_all_conditions_must_pass_for_triggered(self, evaluator):
        """Two conditions: one passes, one fails → not triggered."""
        raw = _minimal_raw_playbook()
        raw["trigger"]["conditions"] = [
            {"metric": "overdue_ratio", "op": ">=", "value": 1.2},
            {"metric": "mock_inventory", "op": ">",  "value": 50},
        ]
        pb = Playbook.from_dict(raw)
        # overdue_ratio passes, inventory fails
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 30})
        assert result.triggered is False
        assert result.trigger_results[0].matched is True
        assert result.trigger_results[1].matched is False


# ===========================================================================
# 4. PLAYBOOK EVALUATOR — FULL EVALUATION
# ===========================================================================

class TestPlaybookEvaluator:

    def _make_evaluator_and_pb(self, **raw_overrides):
        return PlaybookEvaluator(), _playbook_from_minimal(**raw_overrides)

    # ---- Triggered branch ----

    def test_evaluation_triggered_when_all_conditions_pass(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        assert result.triggered is True

    def test_triggered_evaluation_has_checks(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        assert len(result.checks_to_run) > 0

    def test_triggered_evaluation_has_suggested_actions(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        assert len(result.suggested_actions) > 0

    def test_triggered_evaluation_has_likely_causes(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        assert len(result.likely_causes) > 0

    def test_suggested_actions_sorted_by_priority(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["suggested_actions"] = [
            {"action_type": "REMINDER", "priority": 3,
             "recommendation": "r3", "automation_level": "warn_only"},
            {"action_type": "BUNDLE", "priority": 2,
             "recommendation": "r2", "automation_level": "semi_automated"},
            {"action_type": "DISCOUNT", "priority": 1,
             "recommendation": "r1", "automation_level": "semi_automated"},
        ]
        pb = Playbook.from_dict(raw)
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        priorities = [a.priority for a in result.suggested_actions]
        assert priorities == sorted(priorities)

    def test_forbidden_actions_excluded_from_suggested(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["suggested_actions"] = [
            {"action_type": "DISCOUNT", "priority": 1,
             "recommendation": "r1", "automation_level": "semi_automated"},
            {"action_type": "FULL_SHUTDOWN", "priority": 2,
             "recommendation": "bad", "automation_level": "operator_only"},
        ]
        raw["do_not_recommend"] = [
            {"action_type": "FULL_SHUTDOWN", "reason": "Overreaction."}
        ]
        pb = Playbook.from_dict(raw)
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5, "mock_inventory": 100})
        action_types = [a.action_type for a in result.suggested_actions]
        assert "DISCOUNT" in action_types
        assert "FULL_SHUTDOWN" not in action_types

    # ---- Not-triggered branch ----

    def test_not_triggered_when_condition_fails(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        assert result.triggered is False

    def test_not_triggered_checks_are_empty(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        assert result.checks_to_run == []

    def test_not_triggered_suggested_actions_are_empty(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        assert result.suggested_actions == []

    def test_not_triggered_likely_causes_are_empty(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        assert result.likely_causes == []

    # ---- Guardrails always present ----

    def test_do_not_recommend_present_when_triggered(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["do_not_recommend"] = [{"action_type": "X", "reason": "Bad."}]
        pb = Playbook.from_dict(raw)
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        assert len(result.do_not_recommend) == 1

    def test_do_not_recommend_present_when_not_triggered(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["do_not_recommend"] = [{"action_type": "X", "reason": "Bad."}]
        pb = Playbook.from_dict(raw)
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        assert len(result.do_not_recommend) == 1

    def test_ai_failure_modes_always_present(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["common_ai_failure_modes"] = [
            {"failure_mode": "Blames creative.", "guard": "Check CVR first."}
        ]
        pb = Playbook.from_dict(raw)
        for metrics in [{"overdue_ratio": 1.5}, {"overdue_ratio": 0.5}]:
            result = evaluator.evaluate(pb, metrics)
            assert len(result.common_ai_failure_modes) == 1

    # ---- evaluate_all ----

    def test_evaluate_all_returns_dict_keyed_by_rule_id(self, evaluator, registry):
        results = evaluator.evaluate_all(registry, {"overdue_ratio": 1.5, "mock_inventory": 120})
        assert isinstance(results, dict)
        assert "rule_replenishment_discount_v1" in results

    def test_evaluate_all_preserves_all_playbooks(self, evaluator, registry):
        results = evaluator.evaluate_all(registry, {})
        assert len(results) == len(registry)

    # ---- to_dict ----

    def test_to_dict_is_json_serialisable(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        d = result.to_dict()
        # Must not raise
        json.dumps(d)

    def test_to_dict_contains_expected_top_level_keys(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        d = result.to_dict()
        for key in ("rule_id", "triggered", "confidence", "validation_status",
                    "action_lever", "pattern_detected", "diagnosis_summary",
                    "trigger_results", "likely_causes", "checks_to_run",
                    "suggested_actions", "do_not_recommend",
                    "common_ai_failure_modes", "expected_impact_proxy"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_trigger_results_is_list(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        assert isinstance(result.to_dict()["trigger_results"], list)

    # ---- summarize_for_llm ----

    def test_summarize_for_llm_triggered_contains_playbook_id(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        summary = result.summarize_for_llm()
        assert "rule_test_unit_v1" in summary

    def test_summarize_for_llm_triggered_contains_do_not_recommend(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["do_not_recommend"] = [{"action_type": "FORBIDDEN_ACTION", "reason": "Bad."}]
        pb = Playbook.from_dict(raw)
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        summary = result.summarize_for_llm()
        assert "FORBIDDEN_ACTION" in summary

    def test_summarize_for_llm_triggered_contains_failure_modes(self, evaluator):
        raw = _minimal_raw_playbook()
        raw["common_ai_failure_modes"] = [
            {"failure_mode": "Blames creative fatigue.", "guard": "Check CVR first."}
        ]
        pb = Playbook.from_dict(raw)
        result = evaluator.evaluate(pb, {"overdue_ratio": 1.5})
        summary = result.summarize_for_llm()
        assert "Blames creative fatigue" in summary

    def test_summarize_for_llm_not_triggered_says_not_triggered(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        summary = result.summarize_for_llm()
        assert "NOT TRIGGERED" in summary

    def test_summarize_for_llm_not_triggered_mentions_unmet_condition(self, evaluator):
        pb = _playbook_from_minimal()
        result = evaluator.evaluate(pb, {"overdue_ratio": 0.5})
        summary = result.summarize_for_llm()
        assert "overdue_ratio" in summary


# ===========================================================================
# 5. INTEGRATION — real playbook files, end-to-end
# ===========================================================================

class TestIntegration:

    # ---- Replenishment playbook ----

    def test_replenishment_triggers_on_valid_metrics(self, evaluator, replenishment_playbook):
        result = evaluator.evaluate(
            replenishment_playbook,
            {"overdue_ratio": 1.45, "mock_inventory": 120},
        )
        assert result.triggered is True

    def test_replenishment_does_not_trigger_low_overdue_ratio(
        self, evaluator, replenishment_playbook
    ):
        result = evaluator.evaluate(
            replenishment_playbook,
            {"overdue_ratio": 0.95, "mock_inventory": 120},
        )
        assert result.triggered is False

    def test_replenishment_does_not_trigger_low_inventory(
        self, evaluator, replenishment_playbook
    ):
        result = evaluator.evaluate(
            replenishment_playbook,
            {"overdue_ratio": 1.45, "mock_inventory": 30},
        )
        assert result.triggered is False

    def test_replenishment_triggered_has_discount_action(
        self, evaluator, replenishment_playbook
    ):
        result = evaluator.evaluate(
            replenishment_playbook,
            {"overdue_ratio": 1.45, "mock_inventory": 120},
        )
        action_types = [a.action_type for a in result.suggested_actions]
        assert "DISCOUNT" in action_types

    def test_replenishment_does_not_recommend_forbidden_actions(
        self, evaluator, replenishment_playbook
    ):
        result = evaluator.evaluate(
            replenishment_playbook,
            {"overdue_ratio": 1.45, "mock_inventory": 120},
        )
        safe_types = {a.action_type for a in result.suggested_actions}
        forbidden_types = {d.action_type for d in result.do_not_recommend}
        assert safe_types.isdisjoint(forbidden_types)

    def test_replenishment_action_lever_correct(
        self, evaluator, replenishment_playbook
    ):
        result = evaluator.evaluate(
            replenishment_playbook, {"overdue_ratio": 1.45, "mock_inventory": 120}
        )
        assert result.action_lever == "replenishment_discount_nudge"

    def test_replenishment_confidence_is_medium(self, replenishment_playbook):
        assert replenishment_playbook.confidence == "medium"

    # ---- CAC spike playbook ----

    def test_cac_spike_triggers_on_valid_metrics(self, evaluator, cac_spike_playbook):
        result = evaluator.evaluate(
            cac_spike_playbook,
            {
                "cac_7d": 1.28,
                "cac_spike_duration_days": 5,
                "spend_growth_rate": 0.8,
                "customer_growth_rate": 0.4,
            },
        )
        assert result.triggered is True

    def test_cac_spike_does_not_trigger_low_cac(self, evaluator, cac_spike_playbook):
        result = evaluator.evaluate(
            cac_spike_playbook,
            {
                "cac_7d": 1.05,          # below 1.15 threshold
                "cac_spike_duration_days": 5,
                "spend_growth_rate": 0.8,
                "customer_growth_rate": 0.4,
            },
        )
        assert result.triggered is False

    def test_cac_spike_does_not_trigger_when_spend_not_above_customer_growth(
        self, evaluator, cac_spike_playbook
    ):
        result = evaluator.evaluate(
            cac_spike_playbook,
            {
                "cac_7d": 1.28,
                "cac_spike_duration_days": 5,
                "spend_growth_rate": 0.3,     # NOT > customer_growth_rate
                "customer_growth_rate": 0.4,
            },
        )
        assert result.triggered is False

    def test_cac_spike_skips_reference_metric_when_absent(
        self, evaluator, cac_spike_playbook
    ):
        """If customer_growth_rate is absent, the condition is skipped → not triggered."""
        result = evaluator.evaluate(
            cac_spike_playbook,
            {
                "cac_7d": 1.28,
                "cac_spike_duration_days": 5,
                "spend_growth_rate": 0.8,
                # customer_growth_rate intentionally absent
            },
        )
        assert result.triggered is False
        skipped_conditions = [r for r in result.trigger_results if r.skipped]
        assert len(skipped_conditions) >= 1

    def test_cac_spike_recommends_spend_reduction(self, evaluator, cac_spike_playbook):
        result = evaluator.evaluate(
            cac_spike_playbook,
            {
                "cac_7d": 1.28,
                "cac_spike_duration_days": 5,
                "spend_growth_rate": 0.8,
                "customer_growth_rate": 0.4,
            },
        )
        action_types = [a.action_type for a in result.suggested_actions]
        assert "REDUCE_SPEND_ON_AFFECTED_AD_SET" in action_types

    def test_cac_spike_does_not_recommend_full_ad_set_shutoff(
        self, evaluator, cac_spike_playbook
    ):
        result = evaluator.evaluate(
            cac_spike_playbook,
            {
                "cac_7d": 1.28,
                "cac_spike_duration_days": 5,
                "spend_growth_rate": 0.8,
                "customer_growth_rate": 0.4,
            },
        )
        safe_types = {a.action_type for a in result.suggested_actions}
        assert "TURN_OFF_AD_SET_COMPLETELY" not in safe_types

    def test_cac_spike_confidence_is_high(self, cac_spike_playbook):
        assert cac_spike_playbook.confidence == "high"

    def test_cac_spike_action_lever_correct(self, cac_spike_playbook):
        assert cac_spike_playbook.action_lever == "ad_spend_reduction_plus_lp_fix"

    # ---- Pipeline: load_directory → evaluate_all ----

    def test_full_pipeline_load_and_evaluate_all(self, loader, evaluator):
        registry = loader.load_directory(PLAYBOOKS_DIR)
        metrics = {
            "overdue_ratio": 1.45,
            "mock_inventory": 120,
            "cac_7d": 1.28,
            "cac_spike_duration_days": 5,
            "spend_growth_rate": 0.8,
            "customer_growth_rate": 0.4,
        }
        results = evaluator.evaluate_all(registry, metrics)
        assert len(results) == len(registry)
        for rule_id, result in results.items():
            assert result.rule_id == rule_id
            assert result.triggered is True

    def test_full_pipeline_to_dict_is_json_serialisable(self, loader, evaluator):
        registry = loader.load_directory(PLAYBOOKS_DIR)
        metrics = {"overdue_ratio": 1.45, "mock_inventory": 120,
                   "cac_7d": 1.28, "cac_spike_duration_days": 5,
                   "spend_growth_rate": 0.8, "customer_growth_rate": 0.4}
        results = evaluator.evaluate_all(registry, metrics)
        for result in results.values():
            serialised = json.dumps(result.to_dict())
            assert len(serialised) > 0

    def test_full_pipeline_llm_summaries_contain_playbook_ids(self, loader, evaluator):
        registry = loader.load_directory(PLAYBOOKS_DIR)
        metrics = {"overdue_ratio": 1.45, "mock_inventory": 120,
                   "cac_7d": 1.28, "cac_spike_duration_days": 5,
                   "spend_growth_rate": 0.8, "customer_growth_rate": 0.4}
        results = evaluator.evaluate_all(registry, metrics)
        for rule_id, result in results.items():
            summary = result.summarize_for_llm()
            assert rule_id in summary
