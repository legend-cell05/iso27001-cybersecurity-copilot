"""Applicability decisions, implementation state, and the clause requirements.

Between them, ``applicable`` and ``justification`` are the Statement of
Applicability -- the document a certification audit opens first. Two rules are
enforced rather than encouraged:

* **an exclusion carries its reason.** A control excluded without a written
  justification is the single most common reason an SoA comes back. The
  database refuses the row;
* **the decision and the status agree.** A control cannot be excluded and
  implemented, or applicable and marked "excluded". Those two fields drift
  apart in every spreadsheet-based SoA eventually, and a CHECK constraint is
  cheaper than noticing during the audit.

``maturity`` is this project's own 0-5 scale, not the standard's -- the
standard has no maturity model. It exists because "implemented" is binary and
organisations are not: a control can be in place and operating from memory, and
a security manager needs to see which of them are.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.config import Settings, get_settings
from sentinel.db.engine import get_engine
from sentinel.exceptions import DatabaseError, RiskModelError
from sentinel.logging_config import get_logger

logger = get_logger(__name__)

STATUSES = ("not_started", "planned", "in_progress", "implemented", "excluded")
REQUIREMENT_STATUSES = ("not_started", "in_progress", "met")

MATURITY_LABELS = {
    0: "nothing in place",
    1: "ad hoc -- happens when someone remembers",
    2: "repeatable -- the same person does it the same way",
    3: "defined -- written down and followed by anyone",
    4: "measured -- its effectiveness is monitored",
    5: "optimising -- improved on the basis of what is measured",
}


@dataclass(frozen=True)
class ControlState:
    """What the organisation has decided and done about one control."""

    control_id: str
    applicable: bool = True
    justification: str | None = None
    status: str = "not_started"
    owner_role: str | None = None
    maturity: int = 0
    implemented_on: dt.date | None = None
    last_reviewed: dt.date | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise RiskModelError(f"{self.control_id}: unknown status {self.status!r}")
        if not 0 <= self.maturity <= 5:
            raise RiskModelError(
                f"{self.control_id}: maturity {self.maturity} is outside the 0-5 scale"
            )
        if not self.applicable and not (self.justification or "").strip():
            raise RiskModelError(
                f"{self.control_id}: an excluded control needs a written justification -- "
                "it is the first thing a certification audit asks for"
            )
        if self.applicable and self.status == "excluded":
            raise RiskModelError(
                f"{self.control_id}: an applicable control cannot have status 'excluded'"
            )
        if not self.applicable and self.status != "excluded":
            raise RiskModelError(
                f"{self.control_id}: an inapplicable control must have status 'excluded', "
                f"not {self.status!r}"
            )
        if self.status == "implemented" and self.implemented_on is None:
            raise RiskModelError(f"{self.control_id}: 'implemented' is a claim with a date on it")

    @property
    def is_implemented(self) -> bool:
        return self.applicable and self.status == "implemented"

    @property
    def maturity_label(self) -> str:
        return MATURITY_LABELS[self.maturity]

    def as_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "applicable": self.applicable,
            "justification": self.justification,
            "status": self.status,
            "owner_role": self.owner_role,
            "maturity": self.maturity,
            "implemented_on": self.implemented_on,
            "last_reviewed": self.last_reviewed,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RequirementState:
    """What the organisation has done about one management-system requirement."""

    requirement_id: str
    status: str = "not_started"
    owner_role: str | None = None
    completed_on: dt.date | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.status not in REQUIREMENT_STATUSES:
            raise RiskModelError(f"{self.requirement_id}: unknown status {self.status!r}")
        if self.status == "met" and self.completed_on is None:
            raise RiskModelError(f"{self.requirement_id}: 'met' is a claim with a date on it")

    @property
    def is_met(self) -> bool:
        return self.status == "met"

    def as_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "status": self.status,
            "owner_role": self.owner_role,
            "completed_on": self.completed_on,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def upsert_control_states(states: list[ControlState], settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not states:
        return 0
    statement = text(
        f"""INSERT INTO {settings.schema_name}.control_state
            (control_id, applicable, justification, status, owner_role, maturity,
             implemented_on, last_reviewed, notes, updated_at)
            VALUES (:control_id, :applicable, :justification, :status, :owner_role,
                    :maturity, :implemented_on, :last_reviewed, :notes, now())
            ON CONFLICT (control_id) DO UPDATE SET
                applicable = EXCLUDED.applicable,
                justification = EXCLUDED.justification,
                status = EXCLUDED.status,
                owner_role = EXCLUDED.owner_role,
                maturity = EXCLUDED.maturity,
                implemented_on = EXCLUDED.implemented_on,
                last_reviewed = EXCLUDED.last_reviewed,
                notes = EXCLUDED.notes,
                updated_at = now()"""
    )
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(statement, [state.as_dict() for state in states])
    except SQLAlchemyError as exc:
        raise DatabaseError(f"writing control states failed: {exc}") from exc
    logger.info("control states written", extra={"count": len(states)})
    return len(states)


def upsert_requirement_states(
    states: list[RequirementState], settings: Settings | None = None
) -> int:
    settings = settings or get_settings()
    if not states:
        return 0
    statement = text(
        f"""INSERT INTO {settings.schema_name}.requirement_state
            (requirement_id, status, owner_role, completed_on, notes, updated_at)
            VALUES (:requirement_id, :status, :owner_role, :completed_on, :notes, now())
            ON CONFLICT (requirement_id) DO UPDATE SET
                status = EXCLUDED.status,
                owner_role = EXCLUDED.owner_role,
                completed_on = EXCLUDED.completed_on,
                notes = EXCLUDED.notes,
                updated_at = now()"""
    )
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(statement, [state.as_dict() for state in states])
    except SQLAlchemyError as exc:
        raise DatabaseError(f"writing requirement states failed: {exc}") from exc
    return len(states)


def load_control_states(settings: Settings | None = None) -> dict[str, ControlState]:
    """Every recorded control decision, keyed by control identifier."""
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT control_id, applicable, justification, status, owner_role,
                                   maturity, implemented_on, last_reviewed, notes
                            FROM {settings.schema_name}.control_state"""
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading control states failed: {exc}") from exc
    return {row["control_id"]: ControlState(**dict(row)) for row in rows}


def load_requirement_states(settings: Settings | None = None) -> dict[str, RequirementState]:
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT requirement_id, status, owner_role, completed_on, notes
                            FROM {settings.schema_name}.requirement_state"""
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading requirement states failed: {exc}") from exc
    return {row["requirement_id"]: RequirementState(**dict(row)) for row in rows}


def record_activity(
    kind: str,
    performed_on: dt.date,
    performed_by: str,
    summary: str,
    *,
    covers: list[str] | None = None,
    findings_raised: int = 0,
    settings: Settings | None = None,
) -> int:
    """Record an internal audit, management review or similar activity."""
    import json

    settings = settings or get_settings()
    try:
        with get_engine(settings).begin() as conn:
            activity_id: int = conn.execute(
                text(
                    f"""INSERT INTO {settings.schema_name}.management_activity
                        (kind, performed_on, performed_by, summary, covers, findings_raised)
                        VALUES (:kind, :performed_on, :performed_by, :summary,
                                CAST(:covers AS JSONB), :findings_raised)
                        RETURNING activity_id"""
                ),
                {
                    "kind": kind,
                    "performed_on": performed_on,
                    "performed_by": performed_by,
                    "summary": summary,
                    "covers": json.dumps(covers or []),
                    "findings_raised": findings_raised,
                },
            ).scalar_one()
    except SQLAlchemyError as exc:
        raise DatabaseError(f"recording the activity failed: {exc}") from exc
    return activity_id


def latest_activity(kind: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """The most recent activity of one kind, or None if it has never happened.

    "Never happened" and "happened a long time ago" are different findings, so
    this returns None rather than an empty record.
    """
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            row = (
                conn.execute(
                    text(
                        f"""SELECT activity_id, kind, performed_on, performed_by, summary,
                                   covers, findings_raised
                            FROM {settings.schema_name}.management_activity
                            WHERE kind = :kind
                            ORDER BY performed_on DESC
                            LIMIT 1"""
                    ),
                    {"kind": kind},
                )
                .mappings()
                .first()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading activities failed: {exc}") from exc
    return dict(row) if row else None
