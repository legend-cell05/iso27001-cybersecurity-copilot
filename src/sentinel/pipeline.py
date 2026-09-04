"""The operations, composed.

Every command in the CLI and every route in the API calls something here, and
nothing here knows about either. That separation is what stops the readiness
verdict from depending on whether it was asked for over HTTP or at a terminal
-- which is the sort of difference that surfaces as "it passed locally".
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from sentinel.assessment.engine import Assessment, build_snapshot, run_assessment
from sentinel.catalogue import Catalogue, get_catalogue
from sentinel.config import Settings, get_settings
from sentinel.db.schema import initialise_database
from sentinel.demo import Profile, generate
from sentinel.exceptions import ReadinessBlocked
from sentinel.exports.reports import render_dossier, render_findings, render_treatment_plan
from sentinel.exports.soa import render_soa, render_soa_csv
from sentinel.exports.writer import write_export
from sentinel.logging_config import get_logger

logger = get_logger(__name__)


def prepare(settings: Settings | None = None) -> dict[str, int]:
    """Create the schema and its tables. Safe to re-run."""
    settings = settings or get_settings()
    return initialise_database(settings)


def seed_demo(profile: Profile = "gaps", settings: Settings | None = None) -> dict[str, Any]:
    """Populate the registers with the simulated organisation."""
    settings = settings or get_settings()
    prepare(settings)
    return generate(profile, settings).as_dict()


def assess(
    settings: Settings | None = None,
    *,
    today: dt.date | None = None,
    persist: bool = True,
) -> Assessment:
    """Run every rule and the gate, and keep the result."""
    settings = settings or get_settings()
    return run_assessment(settings, today=today, persist=persist)


def declare_ready(
    assessment: Assessment,
    *,
    force: bool = False,
    reason: str | None = None,
) -> Assessment:
    """Assert that the ISMS is ready for a certification audit.

    Raises :class:`ReadinessBlocked` unless the gate passed, which the CLI
    turns into exit code 3. ``force`` overrides it, and the override needs a
    reason -- recorded on the assessment, where an auditor reading the history
    will see both the refusal and the decision to proceed anyway.
    """
    if assessment.decision.ready:
        return assessment
    if not force:
        assessment.decision.raise_if_blocked()
    if not (reason or "").strip():
        raise ReadinessBlocked(
            "an override needs a reason; it is written onto the assessment and read "
            "by whoever asks why this was declared ready"
        )
    assessment.forced = True
    assessment.forced_reason = reason
    logger.warning(
        "readiness declared over a refused gate",
        extra={
            "assessment_id": assessment.assessment_id,
            "failed": [check.name for check in assessment.decision.failed],
            "reason": reason,
        },
    )
    return assessment


def export_all(
    assessment: Assessment,
    settings: Settings | None = None,
    *,
    catalogue: Catalogue | None = None,
    today: dt.date | None = None,
) -> dict[str, Path]:
    """Write every document for this assessment, hashed and recorded."""
    settings = settings or get_settings()
    catalogue = catalogue or get_catalogue(settings)
    snapshot = build_snapshot(settings, catalogue, today)
    stamp = assessment.run_at.date().isoformat()
    suffix = assessment.assessment_id or "draft"

    documents: list[tuple[str, str, str]] = [
        ("soa", f"soa-{stamp}-{suffix}.md", render_soa(assessment, snapshot, settings)),
        ("soa", f"soa-{stamp}-{suffix}.csv", render_soa_csv(assessment, snapshot)),
        (
            "risk_treatment_plan",
            f"risk-treatment-plan-{stamp}-{suffix}.md",
            render_treatment_plan(assessment, snapshot, settings),
        ),
        ("findings", f"findings-{stamp}-{suffix}.md", render_findings(assessment, snapshot)),
        (
            "audit_dossier",
            f"audit-dossier-{stamp}-{suffix}.md",
            render_dossier(assessment, snapshot, settings),
        ),
    ]

    written: dict[str, Path] = {}
    for kind, filename, content in documents:
        path, _ = write_export(
            content,
            kind=kind,
            filename=filename,
            assessment_id=assessment.assessment_id,
            settings=settings,
        )
        written[filename] = path
    return written


def full_cycle(
    profile: Profile = "gaps",
    settings: Settings | None = None,
    *,
    today: dt.date | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Seed, assess, export, and declare -- what the CI job runs.

    Returns rather than raises on a refused gate, because the caller (the CLI)
    owns the exit code and the API owns the status code, and this function
    should not decide either.
    """
    settings = settings or get_settings()
    seeded = seed_demo(profile, settings)
    assessment = assess(settings, today=today)
    blocked: str | None = None
    try:
        declare_ready(assessment, force=force, reason=reason)
    except ReadinessBlocked as exc:
        blocked = str(exc)
    exports = export_all(assessment, settings, today=today)
    return {
        "seeded": seeded,
        "assessment": assessment.as_dict(),
        "exports": sorted(str(path.name) for path in exports.values()),
        "blocked": blocked,
    }
