"""The asset register.

ISO 27001 does not mandate an asset-based risk method, and this project uses
one anyway, for a practical reason: a risk with no subject is a sentence. "Data
could be leaked" cannot be assigned an owner, a likelihood anybody will argue
about, or a control that plausibly treats it. "The customer database could be
read by a former employee" can.

``owner_role`` is a role and not a person on purpose. People leave, and an
asset whose owner left has no owner -- which is precisely the finding an
auditor raises, and precisely the one a register full of names hides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.config import Settings, get_settings
from sentinel.db.engine import get_engine
from sentinel.exceptions import DatabaseError, RiskModelError
from sentinel.logging_config import get_logger

logger = get_logger(__name__)

KINDS = ("information", "software", "hardware", "service", "people", "site")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


@dataclass(frozen=True)
class Asset:
    """Something the organisation is protecting."""

    asset_id: str
    name: str
    kind: str
    owner_role: str
    classification: str
    criticality: int
    site: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise RiskModelError(f"{self.asset_id}: unknown asset kind {self.kind!r}")
        if self.classification not in CLASSIFICATIONS:
            raise RiskModelError(f"{self.asset_id}: unknown classification {self.classification!r}")
        if not 1 <= self.criticality <= 5:
            raise RiskModelError(
                f"{self.asset_id}: criticality {self.criticality} is outside the 1-5 scale"
            )
        if not self.owner_role.strip():
            raise RiskModelError(f"{self.asset_id}: an asset with no owner has no owner")

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "kind": self.kind,
            "owner_role": self.owner_role,
            "classification": self.classification,
            "criticality": self.criticality,
            "site": self.site,
            "description": self.description,
        }


def upsert_assets(assets: list[Asset], settings: Settings | None = None) -> int:
    """Insert or update assets by identifier."""
    settings = settings or get_settings()
    if not assets:
        return 0
    statement = text(
        f"""INSERT INTO {settings.schema_name}.asset
            (asset_id, name, kind, owner_role, classification, criticality, site, description)
            VALUES (:asset_id, :name, :kind, :owner_role, :classification,
                    :criticality, :site, :description)
            ON CONFLICT (asset_id) DO UPDATE SET
                name = EXCLUDED.name,
                kind = EXCLUDED.kind,
                owner_role = EXCLUDED.owner_role,
                classification = EXCLUDED.classification,
                criticality = EXCLUDED.criticality,
                site = EXCLUDED.site,
                description = EXCLUDED.description"""
    )
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(statement, [asset.as_dict() for asset in assets])
    except SQLAlchemyError as exc:
        raise DatabaseError(f"writing assets failed: {exc}") from exc
    logger.info("assets written", extra={"count": len(assets)})
    return len(assets)


def list_assets(settings: Settings | None = None) -> list[Asset]:
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT asset_id, name, kind, owner_role, classification,
                                   criticality, site, description
                            FROM {settings.schema_name}.asset
                            ORDER BY asset_id"""
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as exc:
        raise DatabaseError(f"reading assets failed: {exc}") from exc
    return [Asset(**dict(row)) for row in rows]


def asset_count(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            total: int = conn.execute(
                text(f"SELECT COUNT(*) FROM {settings.schema_name}.asset")
            ).scalar_one()
    except SQLAlchemyError as exc:
        raise DatabaseError(f"counting assets failed: {exc}") from exc
    return total
