"""
playbook_engine
===============
Playbook-as-Code for the CPG Decision Engine.

Loads structured YAML/JSON playbooks, validates them against the canonical
playbook schema, and evaluates their trigger conditions against a metrics
context to produce structured diagnostic recommendations.

Public API
----------
    from playbook_engine import PlaybookLoader, PlaybookEvaluator

    loader    = PlaybookLoader()
    evaluator = PlaybookEvaluator()

    # Load a single playbook
    pb = loader.load("PLAYBOOKS/rule_replenishment_discount_v1.yaml")

    # Load all playbooks in the PLAYBOOKS/ directory
    registry = loader.load_directory()

    # Evaluate against live metrics
    result = evaluator.evaluate(pb, metrics={"overdue_ratio": 1.45, "mock_inventory": 120})
    if result.triggered:
        print(result.summarize_for_llm())
"""

from .evaluator import PlaybookEvaluation, PlaybookEvaluator, TriggerConditionResult
from .loader import PlaybookLoader, PlaybookValidationError
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

__all__ = [
    # Core workflow
    "PlaybookLoader",
    "PlaybookEvaluator",
    # Result types
    "PlaybookEvaluation",
    "TriggerConditionResult",
    # Exceptions
    "PlaybookValidationError",
    # Model types (for type hints in downstream code)
    "Playbook",
    "TriggerCondition",
    "SuggestedAction",
    "Check",
    "LikelyCause",
    "DoNotRecommend",
    "AIFailureMode",
    "ExpectedImpactProxy",
]
