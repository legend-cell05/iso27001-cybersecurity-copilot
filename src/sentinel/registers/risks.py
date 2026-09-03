"""The risk register, and the arithmetic the whole assessment rests on.

Three ideas are worth stating plainly, because they are where compliance tools
usually go soft.

**Inherent and residual are different numbers, and the difference has to be
earned.** Inherent risk is likelihood times impact before treatment. Residual
risk is the same product after the controls that treat it are *implemented* --
not planned, not in progress. A register where every risk is green because the
treatment plan exists is the most common form of comfortable fiction, and the
engine reports it as a finding rather than a score.

**Appetite is a business decision, so it is configuration.** ``risk_appetite``
is the level at or below which a residual risk needs no further treatment, and
``risk_escalation`` is the level above which accepting one requires top
management to have signed. Both are environment settings because they are
argued about in a meeting, not derived from anything.

**An acceptance covers a number, not a risk.** A risk accepted at a residual
score of 6 and later re-scored to 15 is not an accepted risk any more; it is
an unaccepted one carrying an out-of-date signature. The acceptance records
the score it was signed against, and the engine compares the two.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.config import Settings, get_settings
from sentinel.db.engine import get_engine
from sentinel.exceptions import DatabaseError, RiskModelError
from sentinel.logging_config import get_logger

logger = get_logger(__name__)

TREATMENTS = ("modify", "retain", "avoid", "share")
STATUSES = ("open", "treated", "accepted", "closed")

# The scales. Five points each, so the product runs 1-25. Five rather than
# three because three collapses every interesting distinction into "medium",
# and rather than ten because nobody can defend the difference between a 6 and
# a 7 in a meeting.
SCALE_MIN, SCALE_MAX = 1, 5


@dataclass(frozen=True)
class Acceptance:
    """A signed decision to live with a residual risk."""

    risk_id: str
    accepted_by: str
    accepted_role: str
    rationale: str
    residual_at_acceptance: int
    accepted_on: dt.date
    expires_on: dt.date | None = None
    acceptance_id: int | None = None

    def is_expired(self, today: dt.date | None = None) -> bool:
        if self.expires_on is None:
            return False
        return self.expires_on < (today or dt.date.today())

    def covers(self, residual_score: int | None) -> bool:
        """Whether this acceptance still covers the risk's current score.

        A signature obtained for a score of 6 does not cover a score of 15.
        Re-scoring a risk upward silently invalidates its acceptance, and
        saying so is the entire reason this method exists.
        """
        if residual_score is None:
            return False
        return residual_score <= self.residual_at_acceptance


@dataclass(frozen=True)
class Risk:
    """One risk, with both scores and the controls that treat it."""

    risk_id: str
    title: str
    threat: str
    vulnerability: str
    owner_role: str
    likelihood: int
    impact: int
    asset_id: str | None = None
    description: str | None = None
    treatment: str = "modify"
    residual_likelihood: int | None = None
    residual_impact: int | None = None
    status: str = "open"
    identified_on: dt.date = field(default_factory=dt.date.today)
    review_due: dt.date | None = None
    controls: tuple[str, ...] = ()
    acceptance: Acceptance | None = None

    def __post_init__(self) -> None:
        for name, value in (("likelihood", self.likelihood), ("impact", self.impact)):
            if not SCALE_MIN <= value <= SCALE_MAX:
                raise RiskModelError(
                    f"{self.risk_id}: {name} {value} is outside the {SCALE_MIN}-{SCALE_MAX} scale"
                )
        if (self.residual_likelihood is None) != (self.residual_impact is None):
            raise RiskModelError(
                f"{self.risk_id}: a residual score needs both factors or neither; "
                "half of one is not a number anyone can act on"
            )
        if self.treatment not in TREATMENTS:
            raise RiskModelError(f"{self.risk_id}: unknown treatment {self.treatment!r}")
        if self.status not in STATUSES:
            raise RiskModelError(f"{self.risk_id}: unknown status {self.status!r}")
        if self.residual_score is not None and self.residual_score > self.inherent_score:
            raise RiskModelError(
                f"{self.risk_id}: residual risk ({self.residual_score}) is above inherent "
                f"({self.inherent_score}) -- a treatment cannot make a risk worse"
            )

    # --- the arithmetic ---------------------------------------------------

    @property
    def inherent_score(self) -> int:
        return self.likelihood * self.impact

    @property
    def residual_score(self) -> int | None:
        if self.residual_likelihood is None or self.residual_impact is None:
            return None
        return self.residual_likelihood * self.residual_impact

    @property
    def effective_score(self) -> int:
        """The score to judge this risk by: residual if treated, inherent if not."""
        residual = self.residual_score
        return self.inherent_score if residual is None else residual

    def above_appetite(self, settings: Settings | None = None) -> bool:
        settings = settings or get_settings()
        return self.effective_score > settings.risk_appetite

    def needs_management_signature(self, settings: Settings | None = None) -> bool:
        """Whether accepting this risk requires top management, not a manager."""
        settings = settings or get_settings()
        return self.effective_score >= settings.risk_escalation

    @property
    def band(self) -> str:
        """A label for reports. The bands are this project's, not the standard's."""
        score = self.effective_score
        if score >= 15:
            return "critical"
        if score >= 10:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "title": self.title,
            "description": self.description,
            "asset_id": self.asset_id,
            "threat": self.threat,
            "vulnerability": self.vulnerability,
            "owner_role": self.owner_role,
            "likelihood": self.likelihood,
            "impact": self.impact,
            "treatment": self.treatment,
            "residual_likelihood": self.residual_likelihood,
            "residual_impact": self.residual_impact,
            "status": self.status,
            "identified_on": self.identified_on,
            "review_due": self.review_due,
        }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def upsert_risks(risks: list[Risk], settings: Settings | None = None) -> int:
    """Write risks and their control links."""
    settings = settings or get_settings()
    if not risks:
        return 0
    schema = settings.schema_name
    risk_statement = text(
        f"""INSERT INTO {schema}.risk
            (risk_id, title, description, asset_id, threat, vulnerability, owner_role,
             likelihood, impact, treatment, residual_likelihood, residual_impact,
             status, identified_on, review_due)
            VALUES (:risk_id, :title, :description, :asset_id, :threat, :vulnerability,
                    :owner_role, :likelihood, :impact, :treatment, :residual_likelihood,
                    :residual_impact, :status, :identified_on, :review_due)
            ON CONFLICT (risk_id) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                asset_id = EXCLUDED.asset_id,
                threat = EXCLUDED.threat,
                vulnerability = EXCLUDED.vulnerability,
                owner_role = EXCLUDED.owner_role,
                likelihood = EXCLUDED.likelihood,
                impact = EXCLUDED.impact,
                treatment = EXCLUDED.treatment,
                residual_likelihood = EXCLUDED.residual_likelihood,
                residual_impact = EXCLUDED.residual_impact,
                status = EXCLUDED.status,
                review_due = EXCLUDED.review_due"""
    )
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(risk_statement, [risk.as_dict() for risk in risks])
            for risk in risks:
                conn.execute(
                    text(f"DELETE FROM {schema}.risk_control WHERE risk_id = :risk_id"),
                    {"risk_id": risk.risk_id},
                )
                if risk.controls:
                    conn.execute(
                        text(
                            f"""INSERT INTO {schema}.risk_control (risk_id, control_id)
                                VALUES (:risk_id, :control_id)
                                ON CONFLICT DO NOTHING"""
                        ),
                        [
                            {"risk_id": risk.risk_id, "control_id": control_id}
                            for control_id in risk.controls
                        ],
                    )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"writing risks failed: {exc}") from exc
    logger.info("risks written", extra={"count": len(risks)})
    return len(risks)


def accept_risk(acceptance: Acceptance, settings: Settings | None = None) -> int:
    """Record a signed acceptance, withdrawing any that is currently in force.

    Withdrawing first is deliberate: the partial unique index allows exactly
    one acceptance in force per risk, so a second signature has to replace the
    first rather than sit beside it. Both stay in the table -- the superseded
    one keeps its withdrawal date, because "who accepted this, when, and who
    changed their mind" is a question an auditor does ask.
    """
    settings = settings or get_settings()
    schema = settings.schema_name
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(
                text(
                    f"""UPDATE {schema}.risk_acceptance
                        SET withdrawn_on = CURRENT_DATE
                        WHERE risk_id = :risk_id AND withdrawn_on IS NULL"""
                ),
                {"risk_id": acceptance.risk_id},
            )
            new_id: int = conn.execute(
                text(
                    f"""INSERT INTO {schema}.risk_acceptance
                        (risk_id, accepted_by, accepted_role, accepted_on, expires_on,
                         rationale, residual_at_acceptance)
                        VALUES (:risk_id, :accepted_by, :accepted_role, :accepted_on,
                                :expires_on, :rationale, :residual_at_acceptance)
                        RETURNING acceptance_id"""
                ),
                {
                    "risk_id": acceptance.risk_id,
                    "accepted_by": acceptance.accepted_by,
                    "accepted_role": acceptance.accepted_role,
                    "accepted_on": acceptance.accepted_on,
                    "expires_on": acceptance.expires_on,
                    "rationale": acceptance.rationale,
                    "residual_at_acceptance": acceptance.residual_at_acceptance,
                },
            ).scalar_one()
            conn.execute(
                text(f"UPDATE {schema}.risk SET status = 'accepted' WHERE risk_id = :risk_id"),
                {"risk_id": acceptance.risk_id},
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"recording the acceptance failed: {exc}") from exc
    logger.info(
        "risk accepted",
        extra={"risk": acceptance.risk_id, "by": acceptance.accepted_role},
    )
    return new_id


def list_risks(settings: Settings | None = None) -> list[Risk]:
    """Every risk, with its control links and the acceptance in force."""
    settings = settings or get_settings()
    schema = settings.schema_name
    try:
        with get_engine(settings).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT r.*,
                                   COALESCE(
                                       (SELECT array_agg(rc.control_id ORDER BY rc.control_id)
                                          FROM {schema}.risk_control rc
                                         WHERE rc.risk_id = r.risk_id),
                                       ARRAY[]::TEXT[]
                                   ) AS control_ids
                            FROM {schema}.risk r
                            ORDER BY r.risk_id"""
                    )
                )
                .mappings()
                .all()
            )
            acceptances = (
                conn.execute(
                    text(
                        f"""SELECT acceptance_id, risk_id, accepted_by, accepted_role,
                                   accepted_on, expires_on, rationale, residual_at_acceptance
                            FROM {schema}.risk_acceptance
                            WHERE withdrawn_on IS NULL"""
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading risks failed: {exc}") from exc

    in_force = {
        row["risk_id"]: Acceptance(
            acceptance_id=row["acceptance_id"],
            risk_id=row["risk_id"],
            accepted_by=row["accepted_by"],
            accepted_role=row["accepted_role"],
            accepted_on=row["accepted_on"],
            expires_on=row["expires_on"],
            rationale=row["rationale"],
            residual_at_acceptance=row["residual_at_acceptance"],
        )
        for row in acceptances
    }

    risks: list[Risk] = []
    for row in rows:
        risks.append(
            Risk(
                risk_id=row["risk_id"],
                title=row["title"],
                description=row["description"],
                asset_id=row["asset_id"],
                threat=row["threat"],
                vulnerability=row["vulnerability"],
                owner_role=row["owner_role"],
                likelihood=row["likelihood"],
                impact=row["impact"],
                treatment=row["treatment"],
                residual_likelihood=row["residual_likelihood"],
                residual_impact=row["residual_impact"],
                status=row["status"],
                identified_on=row["identified_on"],
                review_due=row["review_due"],
                controls=tuple(row["control_ids"]),
                acceptance=in_force.get(row["risk_id"]),
            )
        )
    return risks


def risk_posture(risks: list[Risk], settings: Settings | None = None) -> dict[str, Any]:
    """Aggregate numbers for the posture report.

    ``treated`` counts risks that actually carry a residual score, which is a
    different and much smaller number than the count of risks with a treatment
    *plan*. Reporting the second one as though it were the first is how a
    register ends up looking healthier than the organisation is.
    """
    settings = settings or get_settings()
    treated = [r for r in risks if r.residual_score is not None]
    above = [r for r in risks if r.above_appetite(settings)]
    bands: dict[str, int] = {}
    for risk in risks:
        bands[risk.band] = bands.get(risk.band, 0) + 1
    inherent_total = sum(r.inherent_score for r in risks)
    effective_total = sum(r.effective_score for r in risks)
    return {
        "risks": len(risks),
        "treated": len(treated),
        "untreated": len(risks) - len(treated),
        "above_appetite": len(above),
        "accepted": len([r for r in risks if r.acceptance is not None]),
        "bands": bands,
        "inherent_total": inherent_total,
        "effective_total": effective_total,
        # How much of the inherent risk the implemented treatments have
        # actually removed. Zero when nothing has been treated, which is the
        # honest answer at the start of a programme.
        "reduction_pct": (
            round(100.0 * (inherent_total - effective_total) / inherent_total, 1)
            if inherent_total
            else 0.0
        ),
        "appetite": settings.risk_appetite,
        "escalation": settings.risk_escalation,
    }
