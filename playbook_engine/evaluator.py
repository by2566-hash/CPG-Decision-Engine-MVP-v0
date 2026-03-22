"""
playbook_engine/evaluator.py

PlaybookEvaluator: evaluates a Playbook's trigger conditions against a
caller-supplied metrics context dict and returns a structured PlaybookEvaluation.

Design principles:
  - Deterministic: same playbook + same metrics always produce the same result.
  - Safe: missing or unknown metrics produce a skipped condition, never a crash.
  - Minimal: no I/O, no side effects, no network calls.
  - Portable: plain Python stdlib + dataclasses only.
"""
from __future__ import annotations

import dataclasses
import operator as _operator
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import (
    AIFailureMode,
    Check,
    DoNotRecommend,
    ExpectedImpactProxy,
    LikelyCause,
    Playbook,
    SuggestedAction,
    TriggerCondition,
)

# Supported comparison operators mapped to Python operator functions.
_OPS: Dict[str, Any] = {
    ">=": _operator.ge,
    ">":  _operator.gt,
    "<=": _operator.le,
    "<":  _operator.lt,
    "==": _operator.eq,
    "!=": _operator.ne,
}


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------

@dataclass
class TriggerConditionResult:
    """Result of evaluating one TriggerCondition against the metrics context."""
    metric: str
    op: str
    threshold: Any          # resolved numeric threshold (or the reference name if unavailable)
    observed: Any           # value from the metrics dict (None if metric missing)
    matched: bool
    skipped: bool = False   # True when the condition could not be evaluated
    skip_reason: Optional[str] = None


@dataclass
class PlaybookEvaluation:
    """
    Full evaluation result for one playbook against a metrics context.

    triggered=True  → diagnosis, checks, and prioritized safe actions are populated.
    triggered=False → trigger_results explains which conditions failed or were skipped.

    do_not_recommend and common_ai_failure_modes are always included regardless
    of trigger state — they are structural guardrails, not conditional outputs.
    """
    rule_id: str
    triggered: bool
    confidence: str           # "high" | "medium" | "low"
    validation_status: str    # "RECOMMENDATION" | "HYPOTHESIS" | "INSUFFICIENT_DATA"
    action_lever: str

    # Pattern and diagnosis (populated when triggered)
    pattern_detected: str
    diagnosis_summary: str

    # Trigger evaluation detail (always populated)
    trigger_results: List[TriggerConditionResult]

    # Diagnostic outputs (empty when not triggered)
    likely_causes: List[LikelyCause]
    checks_to_run: List[Check]
    suggested_actions: List[SuggestedAction]  # priority-sorted, do_not_recommend excluded

    # Guardrails (always populated)
    do_not_recommend: List[DoNotRecommend]
    common_ai_failure_modes: List[AIFailureMode]
    expected_impact_proxy: ExpectedImpactProxy

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """
        Serialize the evaluation result to a plain dict (JSON-safe primitives).
        Suitable for: merchant card builder, telemetry logging, LLM prompt context.
        """
        return dataclasses.asdict(self)

    # ------------------------------------------------------------------
    # LLM summariser
    # ------------------------------------------------------------------

    def summarize_for_llm(self) -> str:
        """
        Compact structured text for injection into an LLM system prompt.

        When triggered: includes diagnosis, ordered recommendations, do_not_recommend
        guardrails, and known AI failure modes to avoid.
        When not triggered: returns a minimal NOT TRIGGERED notice.

        The LLM must treat this as the authoritative grounding context and must not
        invent actions, discounts, or diagnoses beyond what appears here.
        """
        if not self.triggered:
            failed = [
                f"{r.metric} {r.op} {r.threshold} "
                f"(observed={r.observed}, skipped={r.skipped})"
                for r in self.trigger_results
                if not r.matched
            ]
            return (
                f"[Playbook {self.rule_id}] NOT TRIGGERED.\n"
                f"Unmet conditions: {'; '.join(failed) if failed else 'none recorded'}"
            )

        lines = [
            f"PLAYBOOK: {self.rule_id}",
            f"PATTERN:  {self.pattern_detected}",
            f"CONFIDENCE: {self.confidence}  |  STATUS: {self.validation_status}",
            "",
            f"DIAGNOSIS:",
            f"  {self.diagnosis_summary.strip()}",
            "",
            "RECOMMENDED ACTIONS (priority order):",
        ]

        for a in self.suggested_actions:
            lines.append(
                f"  [{a.priority}] {a.action_type}  ({a.automation_level})"
            )
            lines.append(f"      {a.recommendation.strip()}")
            if a.preconditions:
                lines.append(f"      Preconditions: {', '.join(a.preconditions)}")

        if self.do_not_recommend:
            lines += ["", "DO NOT RECOMMEND (hard guardrails):"]
            for dnr in self.do_not_recommend:
                lines.append(f"  ✗ {dnr.action_type}")
                lines.append(f"    Reason: {dnr.reason.strip()}")

        if self.common_ai_failure_modes:
            lines += ["", "KNOWN AI FAILURE MODES TO AVOID:"]
            for mode in self.common_ai_failure_modes:
                lines.append(f"  FAILURE: {mode.failure_mode.strip()}")
                lines.append(f"  GUARD:   {mode.guard.strip()}")

        lines += [
            "",
            "EXPECTED IMPACT:",
            f"  {self.expected_impact_proxy.description.strip()}",
        ]
        if self.expected_impact_proxy.estimated_improvement:
            lines.append(
                f"  Estimated: {self.expected_impact_proxy.estimated_improvement.strip()}"
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class PlaybookEvaluator:
    """
    Evaluates one or more Playbook objects against a caller-supplied metrics
    context (a plain dict of metric_name -> numeric or string value).

    The evaluator is stateless — create one instance and reuse it freely.

    Usage:
        evaluator = PlaybookEvaluator()

        result = evaluator.evaluate(playbook, metrics={
            "overdue_ratio": 1.45,
            "mock_inventory": 120,
        })
        if result.triggered:
            print(result.summarize_for_llm())

        # Batch evaluation across all loaded playbooks
        results = evaluator.evaluate_all(registry, metrics)
        triggered = [r for r in results.values() if r.triggered]
    """

    def evaluate(
        self,
        playbook: Playbook,
        metrics: Dict[str, Any],
    ) -> PlaybookEvaluation:
        """
        Evaluate all trigger conditions in `playbook` against `metrics`.

        triggered=True only when ALL conditions match and NONE are skipped.
        A skipped condition (missing metric or unsupported op) is treated as
        a non-match to avoid false positives on incomplete data.
        """
        trigger_results = [
            self._eval_condition(cond, metrics)
            for cond in playbook.trigger.conditions
        ]

        triggered = all(r.matched and not r.skipped for r in trigger_results)

        return PlaybookEvaluation(
            rule_id=playbook.rule_id,
            triggered=triggered,
            confidence=playbook.confidence,
            validation_status=playbook.validation_status,
            action_lever=playbook.action_lever,
            pattern_detected=playbook.problem_pattern.name,
            diagnosis_summary=playbook.diagnosis_summary,
            trigger_results=trigger_results,
            # Diagnostic outputs only when triggered
            likely_causes=list(playbook.likely_causes) if triggered else [],
            checks_to_run=list(playbook.checks_to_run) if triggered else [],
            suggested_actions=list(playbook.safe_suggested_actions) if triggered else [],
            # Guardrails always present
            do_not_recommend=list(playbook.do_not_recommend),
            common_ai_failure_modes=list(playbook.common_ai_failure_modes),
            expected_impact_proxy=playbook.expected_impact_proxy,
        )

    def evaluate_all(
        self,
        playbooks: Dict[str, Playbook],
        metrics: Dict[str, Any],
    ) -> Dict[str, PlaybookEvaluation]:
        """
        Evaluate every playbook in a registry dict against the same metrics.

        Returns a dict of  rule_id -> PlaybookEvaluation.
        """
        return {
            rule_id: self.evaluate(pb, metrics)
            for rule_id, pb in playbooks.items()
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _eval_condition(
        self,
        cond: TriggerCondition,
        metrics: Dict[str, Any],
    ) -> TriggerConditionResult:
        """Evaluate one TriggerCondition. Never raises — returns a skipped result on error."""

        # 1. Observed value must be present
        observed = metrics.get(cond.metric)
        if observed is None:
            return TriggerConditionResult(
                metric=cond.metric,
                op=cond.op,
                threshold=cond.value,
                observed=None,
                matched=False,
                skipped=True,
                skip_reason=f"metric '{cond.metric}' not in context",
            )

        # 2. Resolve threshold: numeric literal OR metric-reference string
        if isinstance(cond.value, str):
            ref_val = metrics.get(cond.value)
            if ref_val is None:
                return TriggerConditionResult(
                    metric=cond.metric,
                    op=cond.op,
                    threshold=cond.value,   # keep the name as threshold for diagnostics
                    observed=observed,
                    matched=False,
                    skipped=True,
                    skip_reason=f"reference metric '{cond.value}' not in context",
                )
            threshold = ref_val
        else:
            threshold = cond.value

        # 3. Look up operator
        op_fn = _OPS.get(cond.op)
        if op_fn is None:
            return TriggerConditionResult(
                metric=cond.metric,
                op=cond.op,
                threshold=threshold,
                observed=observed,
                matched=False,
                skipped=True,
                skip_reason=f"unsupported operator '{cond.op}'",
            )

        # 4. Compare (guard against type mismatches)
        try:
            matched = bool(op_fn(observed, threshold))
        except TypeError as exc:
            return TriggerConditionResult(
                metric=cond.metric,
                op=cond.op,
                threshold=threshold,
                observed=observed,
                matched=False,
                skipped=True,
                skip_reason=f"type error: {exc}",
            )

        return TriggerConditionResult(
            metric=cond.metric,
            op=cond.op,
            threshold=threshold,
            observed=observed,
            matched=matched,
        )
