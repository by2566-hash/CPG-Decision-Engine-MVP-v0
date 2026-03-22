"""
playbook_engine/models.py

Typed Python dataclasses that mirror the playbook_schema_v1.json structure.
Each class has a `from_dict` classmethod for building from loaded YAML/JSON.
No external dependencies beyond the standard library.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Leaf / primitive wrappers
# ---------------------------------------------------------------------------

@dataclass
class TriggerCondition:
    """One condition inside a playbook trigger block.

    `value` may be a numeric literal (threshold) or a string naming another
    metric in the context dict (metric-to-metric comparison).
    """
    metric: str
    op: str
    value: Any          # float/int literal  OR  str metric-reference
    relative_to: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> TriggerCondition:
        return cls(
            metric=d["metric"],
            op=d["op"],
            value=d["value"],
            relative_to=d.get("relative_to"),
        )


@dataclass
class Trigger:
    conditions: List[TriggerCondition]
    description: str

    @classmethod
    def from_dict(cls, d: dict) -> Trigger:
        return cls(
            conditions=[TriggerCondition.from_dict(c) for c in d["conditions"]],
            description=d["description"],
        )


@dataclass
class EvidenceStep:
    """One step in the diagnostic evidence chain."""
    step: int
    observation: str
    implication: str
    data_source: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> EvidenceStep:
        return cls(
            step=d["step"],
            observation=d["observation"],
            implication=d["implication"],
            data_source=d.get("data_source"),
        )


@dataclass
class DiagnosticLogic:
    evidence_chain: List[EvidenceStep]
    required_data_sources: Optional[Dict[str, List[str]]] = None

    @classmethod
    def from_dict(cls, d: dict) -> DiagnosticLogic:
        rds_raw = d.get("required_data_sources")
        return cls(
            evidence_chain=[EvidenceStep.from_dict(e) for e in d["evidence_chain"]],
            required_data_sources=rds_raw if isinstance(rds_raw, dict) else None,
        )


@dataclass
class LikelyCause:
    cause: str
    confidence: str     # "high" | "medium" | "low"
    supporting_evidence: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> LikelyCause:
        return cls(
            cause=d["cause"],
            confidence=d["confidence"],
            supporting_evidence=d.get("supporting_evidence"),
        )


@dataclass
class Check:
    """A single verification check the merchant (or system) should run."""
    check_id: str
    description: str
    why: str
    confirm_if: Optional[str] = None
    data_source: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> Check:
        return cls(
            check_id=d["check_id"],
            description=d["description"],
            why=d["why"],
            confirm_if=d.get("confirm_if"),
            data_source=d.get("data_source"),
        )


@dataclass
class SuggestedAction:
    action_type: str
    priority: int
    recommendation: str
    automation_level: str   # "automated"|"semi_automated"|"warn_only"|"operator_only"
    preconditions: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> SuggestedAction:
        return cls(
            action_type=d["action_type"],
            priority=d["priority"],
            recommendation=d["recommendation"],
            automation_level=d["automation_level"],
            preconditions=d.get("preconditions") or [],
        )


@dataclass
class DoNotRecommend:
    """An action type that must never be emitted when this rule fires."""
    action_type: str
    reason: str

    @classmethod
    def from_dict(cls, d: dict) -> DoNotRecommend:
        return cls(action_type=d["action_type"], reason=d["reason"])


@dataclass
class AIFailureMode:
    """A known reasoning anti-pattern for LLM prompt hardening."""
    failure_mode: str
    guard: str

    @classmethod
    def from_dict(cls, d: dict) -> AIFailureMode:
        return cls(failure_mode=d["failure_mode"], guard=d["guard"])


@dataclass
class ExpectedImpactProxy:
    description: str
    key_metrics: List[str] = field(default_factory=list)
    estimated_improvement: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> ExpectedImpactProxy:
        return cls(
            description=d["description"],
            key_metrics=d.get("key_metrics") or [],
            estimated_improvement=d.get("estimated_improvement"),
        )


# ---------------------------------------------------------------------------
# Structural / container objects
# ---------------------------------------------------------------------------

@dataclass
class Module:
    primary: str
    domain: str
    business_model: str
    secondary: Optional[str] = None
    vertical: Optional[str] = None
    channels: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Module:
        return cls(
            primary=d["primary"],
            domain=d["domain"],
            business_model=d["business_model"],
            secondary=d.get("secondary"),
            vertical=d.get("vertical"),
            channels=d.get("channels") or [],
            entities=d.get("entities") or [],
        )


@dataclass
class Context:
    merchant_type: str
    applies_broadly_to: List[str] = field(default_factory=list)
    products_in_scope: List[str] = field(default_factory=list)
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> Context:
        return cls(
            merchant_type=d["merchant_type"],
            applies_broadly_to=d.get("applies_broadly_to") or [],
            products_in_scope=d.get("products_in_scope") or [],
            notes=d.get("notes"),
        )


@dataclass
class ProblemPattern:
    name: str
    business_risk: List[str]
    observations: Optional[dict] = None

    @classmethod
    def from_dict(cls, d: dict) -> ProblemPattern:
        return cls(
            name=d["name"],
            business_risk=d["business_risk"],
            observations=d.get("observations"),
        )


# ---------------------------------------------------------------------------
# Top-level Playbook object
# ---------------------------------------------------------------------------

@dataclass
class Playbook:
    """
    A fully parsed and typed playbook / rule object.

    Built from a YAML or JSON file that conforms to playbook_schema_v1.json.
    Immutable after construction — use PlaybookEvaluator to produce results.
    """
    rule_id: str
    version: str
    module: Module
    context: Context
    problem_pattern: ProblemPattern
    trigger: Trigger
    diagnostic_logic: DiagnosticLogic
    diagnosis_summary: str
    likely_causes: List[LikelyCause]
    checks_to_run: List[Check]
    suggested_actions: List[SuggestedAction]   # already sorted by priority ascending
    expected_impact_proxy: ExpectedImpactProxy
    do_not_recommend: List[DoNotRecommend]
    common_ai_failure_modes: List[AIFailureMode]
    confidence: str           # "high" | "medium" | "low"
    validation_status: str    # "RECOMMENDATION" | "HYPOTHESIS" | "INSUFFICIENT_DATA"
    action_lever: str

    @classmethod
    def from_dict(cls, d: dict) -> Playbook:
        return cls(
            rule_id=d["rule_id"],
            version=str(d["version"]),
            module=Module.from_dict(d["module"]),
            context=Context.from_dict(d["context"]),
            problem_pattern=ProblemPattern.from_dict(d["problem_pattern"]),
            trigger=Trigger.from_dict(d["trigger"]),
            diagnostic_logic=DiagnosticLogic.from_dict(d["diagnostic_logic"]),
            diagnosis_summary=d["diagnosis_summary"],
            likely_causes=[LikelyCause.from_dict(c) for c in d["likely_causes"]],
            checks_to_run=[Check.from_dict(c) for c in d["checks_to_run"]],
            suggested_actions=sorted(
                [SuggestedAction.from_dict(a) for a in d["suggested_actions"]],
                key=lambda a: a.priority,
            ),
            expected_impact_proxy=ExpectedImpactProxy.from_dict(d["expected_impact_proxy"]),
            do_not_recommend=[DoNotRecommend.from_dict(x) for x in d["do_not_recommend"]],
            common_ai_failure_modes=[
                AIFailureMode.from_dict(m) for m in d["common_ai_failure_modes"]
            ],
            confidence=d["confidence"],
            validation_status=d["validation_status"],
            action_lever=d["action_lever"],
        )

    # ------------------------------------------------------------------
    # Computed properties (no side effects)
    # ------------------------------------------------------------------

    @property
    def forbidden_actions(self) -> frozenset:
        """Set of action_type strings that must never be recommended."""
        return frozenset(d.action_type for d in self.do_not_recommend)

    @property
    def safe_suggested_actions(self) -> List[SuggestedAction]:
        """
        Suggested actions filtered to exclude any action_type that appears
        in do_not_recommend. Preserves priority ordering.
        """
        forbidden = self.forbidden_actions
        return [a for a in self.suggested_actions if a.action_type not in forbidden]

    @property
    def minimum_viable_sources(self) -> List[str]:
        """Minimum required data sources, if specified."""
        rds = self.diagnostic_logic.required_data_sources
        if rds and isinstance(rds, dict):
            return rds.get("minimum_viable") or []
        return []
