"""Running an assessment and keeping it.

An assessment is a dated, reproducible statement about the ISMS: this
catalogue, these decisions, these findings, this verdict. It is kept rather
than overwritten because the sequence is itself evidence -- an auditor asking
"how did you know you were ready?" is asking for the row before the one that
said yes.

Everything the rules need is read once into a :class:`RegisterSnapshot` and
passed in. A rule cannot query the database, which is what makes the same rule
behave identically in the CLI, the API and a unit test with no database at all.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.assessment.gate import Check, GateDecision, evaluate_gate
from sentinel.assessment.rules import Finding, RegisterSnapshot, evaluate, summarise
from sentinel.catalogue import Catalogue, get_catalogue
from sentinel.config import Settings, get_settings
from sentinel.db.engine import get_engine
from sentinel.exceptions import AssessmentNotFound, DatabaseError
from sentinel.logging_config import get_logger
from sentinel.registers.controls import (
    latest_activity,
    load_control_states,
    load_requirement_states,
)
from sentinel.registers.evidence import freshness, load_evidence
from sentinel.registers.risks import list_risks, risk_posture

logger = get_logger(__name__)


@dataclass
class Assessment:
    """One run: what was measured, what was found, and what the gate said."""

    assessment_id: int | None
    run_at: dt.datetime
    catalogue_version: str
    catalogue_fingerprint: str
    organisation: str
    scope: str
    totals: dict[str, Any]
    findings: list[Finding]
    decision: GateDecision
    forced: bool = False
    forced_reason: str | None = None

    @property
    def ready(self) -> bool:
        return self.decision.ready or self.forced

    @property
    def blocking(self) -> int:
        return len([finding for finding in self.findings if finding.is_blocking])

    def as_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "run_at": self.run_at.isoformat(),
            "organisation": self.organisation,
            "scope": self.scope,
            "catalogue_version": self.catalogue_version,
            "catalogue_fingerprint": self.catalogue_fingerprint,
            "totals": self.totals,
            "findings": summarise(self.findings),
            "ready": self.ready,
            "forced": self.forced,
            "gate": self.decision.as_dict(),
        }


def build_snapshot(
    settings: Settings | None = None,
    catalogue: Catalogue | None = None,
    today: dt.date | None = None,
) -> RegisterSnapshot:
    """Read every register once."""
    settings = settings or get_settings()
    catalogue = catalogue or get_catalogue(settings)
    today = today or dt.date.today()
    evidence = load_evidence(settings)
    return RegisterSnapshot(
        catalogue=catalogue,
        control_states=load_control_states(settings),
        requirement_states=load_requirement_states(settings),
        risks=list_risks(settings),
        evidence=evidence,
        freshness=freshness(evidence, catalogue, settings, today),
        internal_audit=latest_activity("internal_audit", settings),
        management_review=latest_activity("management_review", settings),
        today=today,
    )


def compute_totals(snapshot: RegisterSnapshot, settings: Settings) -> dict[str, Any]:
    """The numbers the gate and the reports both use.

    ``implemented`` counts only controls that are applicable *and* implemented.
    Counting an excluded control as done -- which a naive "not outstanding"
    query does -- inflates the figure by exactly the number of controls the
    organisation decided to skip.
    """
    catalogue = snapshot.catalogue
    states = snapshot.control_states
    assessed = [states[c.id] for c in catalogue.controls if c.id in states]
    applicable = [state for state in assessed if state.applicable]
    implemented = [state for state in applicable if state.is_implemented]
    evidenced = [
        state
        for state in implemented
        if snapshot.freshness.get(state.control_id, {}).get("count", 0) > 0
        and not snapshot.freshness.get(state.control_id, {}).get("stale", True)
    ]

    by_theme: dict[str, dict[str, int]] = {}
    for control in catalogue.controls:
        bucket = by_theme.setdefault(control.theme, {"total": 0, "applicable": 0, "implemented": 0})
        bucket["total"] += 1
        state = states.get(control.id)
        if state is None or not state.applicable:
            continue
        bucket["applicable"] += 1
        if state.is_implemented:
            bucket["implemented"] += 1

    by_capability: dict[str, dict[str, int]] = {}
    for control in catalogue.controls:
        bucket = by_capability.setdefault(
            control.capability, {"total": 0, "applicable": 0, "implemented": 0}
        )
        bucket["total"] += 1
        state = states.get(control.id)
        if state is None or not state.applicable:
            continue
        bucket["applicable"] += 1
        if state.is_implemented:
            bucket["implemented"] += 1

    requirements = catalogue.requirements
    met = [
        requirement
        for requirement in requirements
        if (met_state := snapshot.requirement_states.get(requirement.id)) is not None
        and met_state.is_met
    ]

    maturity_values = [state.maturity for state in implemented]
    return {
        "controls": len(catalogue.controls),
        "assessed": len(assessed),
        "applicable": len(applicable),
        "excluded": len(assessed) - len(applicable),
        "implemented": len(implemented),
        "evidenced": len(evidenced),
        "implementation_pct": (
            round(100.0 * len(implemented) / len(applicable), 1) if applicable else 0.0
        ),
        "evidence_pct": (
            round(100.0 * len(evidenced) / len(implemented), 1) if implemented else 0.0
        ),
        "mean_maturity": (
            round(sum(maturity_values) / len(maturity_values), 2) if maturity_values else 0.0
        ),
        "by_theme": by_theme,
        "by_capability": by_capability,
        "requirements": len(requirements),
        "requirements_met": len(met),
        "mandatory_requirements": len(catalogue.mandatory_requirements),
        "risk": risk_posture(snapshot.risks, settings),
    }


def run_assessment(
    settings: Settings | None = None,
    *,
    catalogue: Catalogue | None = None,
    today: dt.date | None = None,
    persist: bool = True,
    forced: bool = False,
    forced_reason: str | None = None,
) -> Assessment:
    """Evaluate every rule, run the gate, and keep the result."""
    settings = settings or get_settings()
    catalogue = catalogue or get_catalogue(settings)
    snapshot = build_snapshot(settings, catalogue, today)
    findings = evaluate(snapshot, settings)
    totals = compute_totals(snapshot, settings)
    decision = evaluate_gate(snapshot, findings, totals, settings)

    assessment = Assessment(
        assessment_id=None,
        run_at=dt.datetime.now(tz=dt.UTC),
        catalogue_version=catalogue.version,
        catalogue_fingerprint=catalogue.fingerprint,
        organisation=settings.organisation,
        scope=settings.scope,
        totals=totals,
        findings=findings,
        decision=decision,
        forced=forced,
        forced_reason=forced_reason,
    )
    if persist:
        assessment.assessment_id = _persist(assessment, settings)
    logger.info(
        "assessment complete",
        extra={
            "assessment_id": assessment.assessment_id,
            "ready": assessment.ready,
            "blocking": assessment.blocking,
            "findings": len(findings),
            "implementation_pct": totals["implementation_pct"],
        },
    )
    return assessment


def _persist(assessment: Assessment, settings: Settings) -> int:
    schema = settings.schema_name
    parameters = {
        "risk_appetite": settings.risk_appetite,
        "risk_escalation": settings.risk_escalation,
        "min_implemented_ratio": settings.min_implemented_ratio,
        "max_controls_without_evidence": settings.max_controls_without_evidence,
        "evidence_max_age_days": settings.evidence_max_age_days,
        "require_internal_audit": settings.require_internal_audit,
    }
    try:
        with get_engine(settings).begin() as conn:
            assessment_id: int = conn.execute(
                text(
                    f"""INSERT INTO {schema}.assessment
                        (run_at, organisation, scope, catalogue_version, catalogue_fingerprint,
                         parameters, totals, ready, blocking_findings, forced, forced_reason)
                        VALUES (:run_at, :organisation, :scope, :version, :fingerprint,
                                CAST(:parameters AS JSONB), CAST(:totals AS JSONB),
                                :ready, :blocking, :forced, :forced_reason)
                        RETURNING assessment_id"""
                ),
                {
                    "run_at": assessment.run_at,
                    "organisation": assessment.organisation,
                    "scope": assessment.scope,
                    "version": assessment.catalogue_version,
                    "fingerprint": assessment.catalogue_fingerprint,
                    "parameters": json.dumps(parameters),
                    "totals": json.dumps(assessment.totals, default=str),
                    "ready": assessment.ready,
                    "blocking": assessment.blocking,
                    "forced": assessment.forced,
                    "forced_reason": assessment.forced_reason,
                },
            ).scalar_one()

            if assessment.findings:
                conn.execute(
                    text(
                        f"""INSERT INTO {schema}.finding
                            (assessment_id, rule, severity, subject_kind, subject_id,
                             detail, remediation)
                            VALUES (:assessment_id, :rule, :severity, :subject_kind,
                                    :subject_id, :detail, :remediation)"""
                    ),
                    [
                        {"assessment_id": assessment_id, **finding.as_dict()}
                        for finding in assessment.findings
                    ],
                )
            conn.execute(
                text(
                    f"""INSERT INTO {schema}.readiness_check
                        (assessment_id, name, passed, skipped, detail, value, threshold)
                        VALUES (:assessment_id, :name, :passed, :skipped, :detail,
                                :value, :threshold)"""
                ),
                [
                    {"assessment_id": assessment_id, **check.as_dict()}
                    for check in assessment.decision.checks
                ],
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"saving the assessment failed: {exc}") from exc
    return assessment_id


def get_assessment(assessment_id: int, settings: Settings | None = None) -> dict[str, Any]:
    """One stored assessment, with its findings and checks."""
    settings = settings or get_settings()
    schema = settings.schema_name
    try:
        with get_engine(settings).connect() as conn:
            row = (
                conn.execute(
                    text(f"SELECT * FROM {schema}.assessment WHERE assessment_id = :id"),
                    {"id": assessment_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AssessmentNotFound(f"no assessment {assessment_id}")
            findings = (
                conn.execute(
                    text(
                        f"""SELECT rule, severity, subject_kind, subject_id, detail, remediation
                            FROM {schema}.finding WHERE assessment_id = :id
                            ORDER BY severity, rule, subject_id"""
                    ),
                    {"id": assessment_id},
                )
                .mappings()
                .all()
            )
            checks = (
                conn.execute(
                    text(
                        f"""SELECT name, passed, skipped, detail, value, threshold
                            FROM {schema}.readiness_check WHERE assessment_id = :id
                            ORDER BY check_id"""
                    ),
                    {"id": assessment_id},
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading the assessment failed: {exc}") from exc
    return {
        **dict(row),
        "findings": [dict(finding) for finding in findings],
        "checks": [dict(check) for check in checks],
    }


def latest_assessment(settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            row = (
                conn.execute(
                    text(
                        f"""SELECT assessment_id FROM {settings.schema_name}.assessment
                            ORDER BY run_at DESC LIMIT 1"""
                    )
                )
                .mappings()
                .first()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading assessments failed: {exc}") from exc
    if row is None:
        return None
    return get_assessment(int(row["assessment_id"]), settings)


def assessment_history(limit: int = 10, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Recent assessments, newest first.

    The trend is the point: an organisation whose blocking findings fall from
    31 to 4 over three months is in a different position from one that has run
    the tool once, however similar today's number looks.
    """
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT assessment_id, run_at, catalogue_version, ready,
                                   blocking_findings, forced, totals
                            FROM {settings.schema_name}.assessment
                            ORDER BY run_at DESC LIMIT :limit"""
                    ),
                    {"limit": limit},
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading assessments failed: {exc}") from exc
    return [dict(row) for row in rows]


__all__ = [
    "Assessment",
    "Check",
    "GateDecision",
    "assessment_history",
    "build_snapshot",
    "compute_totals",
    "get_assessment",
    "latest_assessment",
    "run_assessment",
]
