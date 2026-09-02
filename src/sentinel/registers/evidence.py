"""The evidence index.

An implemented control with no evidence is an assertion, and a certification
audit is an exercise in turning assertions into artefacts. This module records
what can be shown, and -- more usefully -- when what can be shown stopped
being worth showing.

Three decisions:

**Evidence is hashed.** ``content_hash`` is the SHA-256 of the artefact as
collected. It turns "this is the policy that was in force in March" from a
memory into a checkable claim, and it costs one read of the file.

**Freshness is per control, not global.** A penetration test report and an
approved policy do not age at the same rate. The catalogue carries
``evidence_valid_days`` per control; a piece of evidence may override it
downward when the organisation's own policy is stricter, never upward.

**The file itself is not stored here.** ``location`` is a path or a URI. A
compliance tool that becomes a document management system is a compliance tool
nobody can deploy next to the document management system they already have.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.catalogue import Catalogue
from sentinel.config import Settings, get_settings
from sentinel.db.engine import get_engine
from sentinel.exceptions import DatabaseError, EvidenceError
from sentinel.logging_config import get_logger

logger = get_logger(__name__)

KINDS = (
    "policy",
    "procedure",
    "record",
    "log",
    "report",
    "screenshot",
    "ticket",
    "certificate",
    "contract",
)


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    """SHA-256 of a file, read in chunks.

    Chunked because evidence is sometimes a 400 MB log export, and a tool that
    needs the artefact in memory to index it is a tool that falls over on the
    one piece of evidence that mattered.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    return digest.hexdigest()


@dataclass(frozen=True)
class Evidence:
    """One artefact supporting one control or clause requirement."""

    subject_id: str
    title: str
    kind: str
    collected_on: dt.date
    collected_by: str
    location: str
    content_hash: str
    subject_kind: str = "control"
    valid_days: int | None = None
    superseded_on: dt.date | None = None
    evidence_id: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise EvidenceError(f"{self.subject_id}: unknown evidence kind {self.kind!r}")
        if self.subject_kind not in ("control", "requirement"):
            raise EvidenceError(f"unknown subject kind {self.subject_kind!r}")
        if len(self.content_hash) != 64 or not all(
            c in "0123456789abcdef" for c in self.content_hash
        ):
            raise EvidenceError(
                f"{self.subject_id}: content_hash must be a SHA-256 hex digest -- "
                "a hash that is not one looks like proof and is not"
            )
        if self.valid_days is not None and self.valid_days <= 0:
            raise EvidenceError(f"{self.subject_id}: valid_days must be positive")

    def expires_on(self, default_valid_days: int) -> dt.date:
        days = self.valid_days or default_valid_days
        return self.collected_on + dt.timedelta(days=days)

    def age_days(self, today: dt.date | None = None) -> int:
        return ((today or dt.date.today()) - self.collected_on).days

    def is_stale(self, default_valid_days: int, today: dt.date | None = None) -> bool:
        return (today or dt.date.today()) > self.expires_on(default_valid_days)

    def days_until_stale(self, default_valid_days: int, today: dt.date | None = None) -> int:
        return (self.expires_on(default_valid_days) - (today or dt.date.today())).days

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "title": self.title,
            "kind": self.kind,
            "collected_on": self.collected_on,
            "collected_by": self.collected_by,
            "location": self.location,
            "content_hash": self.content_hash,
            "valid_days": self.valid_days,
            "superseded_on": self.superseded_on,
        }


def record_evidence(items: list[Evidence], settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not items:
        return 0
    statement = text(
        f"""INSERT INTO {settings.schema_name}.evidence
            (subject_kind, subject_id, title, kind, collected_on, collected_by,
             location, content_hash, valid_days, superseded_on)
            VALUES (:subject_kind, :subject_id, :title, :kind, :collected_on,
                    :collected_by, :location, :content_hash, :valid_days, :superseded_on)"""
    )
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(statement, [item.as_dict() for item in items])
    except SQLAlchemyError as exc:
        raise DatabaseError(f"recording evidence failed: {exc}") from exc
    logger.info("evidence recorded", extra={"count": len(items)})
    return len(items)


def load_evidence(settings: Settings | None = None) -> dict[str, list[Evidence]]:
    """Current evidence, grouped by subject, newest first.

    Superseded evidence is excluded: it is kept in the table because the
    history is itself evidence, but a control is supported by what is current,
    not by what it used to have.
    """
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT evidence_id, subject_kind, subject_id, title, kind,
                                   collected_on, collected_by, location, content_hash,
                                   valid_days, superseded_on
                            FROM {settings.schema_name}.evidence
                            WHERE superseded_on IS NULL
                            ORDER BY subject_id, collected_on DESC"""
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading evidence failed: {exc}") from exc

    grouped: dict[str, list[Evidence]] = {}
    for row in rows:
        item = Evidence(**dict(row))
        grouped.setdefault(item.subject_id, []).append(item)
    return grouped


def freshness(
    evidence: dict[str, list[Evidence]],
    catalogue: Catalogue,
    settings: Settings | None = None,
    today: dt.date | None = None,
) -> dict[str, dict[str, Any]]:
    """Per control: how much evidence, how old, and whether it still counts.

    The "newest piece" is what decides. An organisation with four stale
    artefacts and one collected last week is evidenced; one with forty from
    two years ago is not, however impressive the count looks in a report.
    """
    settings = settings or get_settings()
    today = today or dt.date.today()
    result: dict[str, dict[str, Any]] = {}
    for control in catalogue.controls:
        items = evidence.get(control.id, [])
        default_days = min(control.evidence_valid_days, settings.evidence_max_age_days)
        if not items:
            result[control.id] = {
                "count": 0,
                "newest": None,
                "stale": True,
                "expiring_soon": False,
                "days_remaining": None,
                "valid_days": default_days,
            }
            continue
        newest = max(items, key=lambda item: item.collected_on)
        remaining = newest.days_until_stale(default_days, today)
        result[control.id] = {
            "count": len(items),
            "newest": newest.collected_on,
            "stale": remaining < 0,
            "expiring_soon": 0 <= remaining <= settings.evidence_warn_days,
            "days_remaining": remaining,
            "valid_days": newest.valid_days or default_days,
            "kinds": sorted({item.kind for item in items}),
        }
    return result
