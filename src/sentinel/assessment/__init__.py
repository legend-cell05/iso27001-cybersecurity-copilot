"""Assessment: the finding rules, the readiness gate, and the run record."""

from __future__ import annotations

from sentinel.assessment.engine import (
    Assessment,
    assessment_history,
    build_snapshot,
    compute_totals,
    get_assessment,
    latest_assessment,
    run_assessment,
)
from sentinel.assessment.gate import Check, GateDecision, evaluate_gate
from sentinel.assessment.rules import (
    RULES,
    SEVERITIES,
    Finding,
    RegisterSnapshot,
    evaluate,
    summarise,
)

__all__ = [
    "RULES",
    "SEVERITIES",
    "Assessment",
    "Check",
    "Finding",
    "GateDecision",
    "RegisterSnapshot",
    "assessment_history",
    "build_snapshot",
    "compute_totals",
    "evaluate",
    "evaluate_gate",
    "get_assessment",
    "latest_assessment",
    "run_assessment",
    "summarise",
]
