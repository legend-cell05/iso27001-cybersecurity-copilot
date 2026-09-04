"""A read-only HTTP surface over the posture.

Read-only on purpose. Changing an applicability decision or signing off a risk
acceptance over an unauthenticated HTTP endpoint would be an odd thing for a
compliance tool to offer, and this one has no authentication (see
`docs/security.md`). What it does offer is the thing a dashboard or a CI job
actually wants: the current posture, the findings, and the gate's verdict, as
JSON.

The gate's verdict is also an HTTP status: `/api/v1/readiness` answers 200 when
the gate passes and 409 when it refuses. A monitoring check can then be a URL
rather than a parser.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Response, status

from sentinel import __version__
from sentinel.assessment.engine import (
    assessment_history,
    build_snapshot,
    latest_assessment,
    run_assessment,
)
from sentinel.catalogue import get_catalogue
from sentinel.config import get_settings
from sentinel.db.engine import check_connection
from sentinel.db.schema import schema_exists
from sentinel.exceptions import SentinelError
from sentinel.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="sentinel",
        version=__version__,
        description=(
            "Read-only ISO/IEC 27001:2022 posture for a simulated organisation. "
            "Synthetic data throughout."
        ),
    )

    @app.get("/health", summary="Liveness")
    def health() -> dict[str, Any]:
        reachable = check_connection(settings, retries=1)
        return {
            "status": "ok" if reachable else "degraded",
            "version": __version__,
            "database": reachable,
            "schema": schema_exists(settings) if reachable else False,
            "organisation": settings.organisation,
        }

    @app.get("/api/v1/catalogue", summary="The control catalogue")
    def catalogue() -> dict[str, Any]:
        loaded = get_catalogue(settings)
        return {
            **loaded.summary(),
            "standard": loaded.standard,
            "note": loaded.framework_note.strip(),
        }

    @app.get("/api/v1/posture", summary="Current coverage and risk position")
    def posture() -> dict[str, Any]:
        try:
            assessment = run_assessment(settings, persist=False)
        except SentinelError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        totals = assessment.totals
        return {
            "organisation": assessment.organisation,
            "scope": assessment.scope,
            "catalogue": {
                "version": assessment.catalogue_version,
                "fingerprint": assessment.catalogue_fingerprint,
            },
            "controls": {
                key: totals[key]
                for key in (
                    "controls",
                    "assessed",
                    "applicable",
                    "excluded",
                    "implemented",
                    "evidenced",
                    "implementation_pct",
                    "evidence_pct",
                    "mean_maturity",
                )
            },
            "by_theme": totals["by_theme"],
            "by_capability": totals["by_capability"],
            "requirements": {
                "total": totals["requirements"],
                "met": totals["requirements_met"],
                "mandatory": totals["mandatory_requirements"],
            },
            "risk": totals["risk"],
            "findings": assessment.as_dict()["findings"],
            "ready": assessment.decision.ready,
        }

    @app.get("/api/v1/findings", summary="Findings from the latest stored assessment")
    def findings(severity: str | None = None, rule: str | None = None) -> dict[str, Any]:
        stored = latest_assessment(settings)
        if stored is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no assessment has been run yet")
        rows = stored["findings"]
        if severity:
            rows = [row for row in rows if row["severity"] == severity]
        if rule:
            rows = [row for row in rows if row["rule"] == rule]
        return {
            "assessment_id": stored["assessment_id"],
            "run_at": stored["run_at"],
            "count": len(rows),
            "findings": rows,
        }

    @app.get("/api/v1/readiness", summary="Would the ISMS survive an audit today?")
    def readiness(response: Response) -> dict[str, Any]:
        try:
            assessment = run_assessment(settings, persist=False)
        except SentinelError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        # 409 rather than 200-with-a-flag: a monitoring check should be able to
        # be a URL, and "not ready" is a conflict with the state being asserted.
        if not assessment.decision.ready:
            response.status_code = status.HTTP_409_CONFLICT
        return {
            "ready": assessment.decision.ready,
            "blocking_findings": assessment.blocking,
            **assessment.decision.as_dict(),
        }

    @app.get("/api/v1/risks", summary="The risk register")
    def risks() -> dict[str, Any]:
        snapshot = build_snapshot(settings)
        return {
            "appetite": settings.risk_appetite,
            "escalation": settings.risk_escalation,
            "risks": [
                {
                    "risk_id": risk.risk_id,
                    "title": risk.title,
                    "owner_role": risk.owner_role,
                    "inherent": risk.inherent_score,
                    "residual": risk.residual_score,
                    "band": risk.band,
                    "above_appetite": risk.above_appetite(settings),
                    "controls": list(risk.controls),
                    "acceptance": (
                        None
                        if risk.acceptance is None
                        else {
                            "role": risk.acceptance.accepted_role,
                            "accepted_on": risk.acceptance.accepted_on.isoformat(),
                            "covers_current_score": risk.acceptance.covers(risk.effective_score),
                        }
                    ),
                }
                for risk in sorted(snapshot.risks, key=lambda r: (-r.effective_score, r.risk_id))
            ],
        }

    @app.get("/api/v1/history", summary="Past assessments")
    def history(limit: int = 10) -> dict[str, Any]:
        return {"assessments": assessment_history(limit, settings)}

    return app


app = create_app()
