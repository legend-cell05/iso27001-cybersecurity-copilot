"""Shared fixtures.

The unit tests build a :class:`RegisterSnapshot` by hand and never touch a
database -- which is only possible because the rules take everything they read
as an argument. That constraint was chosen for exactly this reason: a rule
that could query would need a database to test, and rules that are expensive
to test are rules nobody adds.

The integration tests use a real PostgreSQL and their own schema, because what
is worth testing there is the constraints (a residual score above its inherent
one, two acceptances in force for one risk) and a mock would simply agree with
whatever the code did.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from sentinel.assessment.rules import RegisterSnapshot
from sentinel.catalogue import Catalogue, load_catalogue
from sentinel.config import Settings
from sentinel.db.engine import check_connection, dispose_engines
from sentinel.db.schema import drop_schema, initialise_database
from sentinel.registers.controls import ControlState, RequirementState
from sentinel.registers.evidence import Evidence
from sentinel.registers.risks import Risk

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 27)

requires_database = pytest.mark.skipif(
    os.environ.get("SENTINEL_RUN_INTEGRATION") != "1",
    reason="set SENTINEL_RUN_INTEGRATION=1 and provide a PostgreSQL to run these",
)


@pytest.fixture(scope="session")
def catalogue() -> Catalogue:
    """The real catalogue. Loading it in the tests is itself a check."""
    return load_catalogue(REPO_ROOT / "catalogue")


@pytest.fixture(scope="session")
def integration_settings() -> Iterator[Settings]:
    """An isolated ISMS schema, created and dropped around the session."""
    settings = Settings(SENTINEL_SCHEMA="test_isms", data_dir=REPO_ROOT / "data")
    if not check_connection(settings, retries=2):
        pytest.skip("no PostgreSQL available")
    drop_schema(settings)
    initialise_database(settings)
    yield settings
    drop_schema(settings)
    dispose_engines()


# ---------------------------------------------------------------------------
# Hand-built registers for the rule tests
# ---------------------------------------------------------------------------


def make_control_state(control_id: str, **overrides: object) -> ControlState:
    """An implemented, owned, evidenced control -- the boring default.

    Every rule test starts from a state that produces no findings and breaks
    exactly one thing, so a finding in the result is unambiguously caused by
    what the test changed.
    """
    defaults: dict[str, object] = {
        "applicable": True,
        "status": "implemented",
        "owner_role": "Information Security Officer",
        "maturity": 3,
        "implemented_on": TODAY - dt.timedelta(days=90),
        "justification": "Part of the ISMS baseline.",
    }
    defaults.update(overrides)
    return ControlState(control_id=control_id, **defaults)  # type: ignore[arg-type]


def make_evidence(control_id: str, *, age_days: int = 10, kind: str = "policy") -> Evidence:
    return Evidence(
        subject_id=control_id,
        title=f"{control_id} evidence",
        kind=kind,
        collected_on=TODAY - dt.timedelta(days=age_days),
        collected_by="Information Security Officer",
        location=f"evidence/{control_id}.pdf",
        content_hash="a" * 64,
    )


def make_risk(risk_id: str = "RSK-001", **overrides: object) -> Risk:
    defaults: dict[str, object] = {
        "title": "A risk",
        "threat": "A threat",
        "vulnerability": "A weakness",
        "owner_role": "Head of IT",
        "likelihood": 3,
        "impact": 3,
        "residual_likelihood": 2,
        "residual_impact": 2,
        "status": "treated",
        "controls": ("A.5.15",),
        "identified_on": TODAY - dt.timedelta(days=200),
    }
    defaults.update(overrides)
    return Risk(risk_id=risk_id, **defaults)  # type: ignore[arg-type]


@pytest.fixture
def snapshot_factory(catalogue: Catalogue):  # type: ignore[no-untyped-def]
    """Build a snapshot where everything is fine, then break one thing."""

    def build(
        *,
        control_states: dict[str, ControlState] | None = None,
        requirement_states: dict[str, RequirementState] | None = None,
        risks: list[Risk] | None = None,
        evidence: dict[str, list[Evidence]] | None = None,
        internal_audit: dict[str, object] | None = ...,  # type: ignore[assignment]
        management_review: dict[str, object] | None = ...,  # type: ignore[assignment]
        today: dt.date = TODAY,
    ) -> RegisterSnapshot:
        from sentinel.config import Settings as _Settings
        from sentinel.registers.evidence import freshness

        settings = _Settings()
        # `is None` rather than a falsy check: passing an empty dict means
        # "no evidence at all", which is a case several tests need.
        states = (
            control_states
            if control_states is not None
            else {control.id: make_control_state(control.id) for control in catalogue.controls}
        )
        items = (
            evidence
            if evidence is not None
            else {
                control.id: [make_evidence(control.id, kind=control.evidence_expected[0])]
                for control in catalogue.controls
            }
        )
        requirements = requirement_states or {
            requirement.id: RequirementState(
                requirement_id=requirement.id,
                status="met",
                completed_on=today - dt.timedelta(days=30),
            )
            for requirement in catalogue.requirements
        }
        recent = {"performed_on": today - dt.timedelta(days=60), "performed_by": "auditor"}
        return RegisterSnapshot(
            catalogue=catalogue,
            control_states=states,
            requirement_states=requirements,
            risks=risks if risks is not None else [],
            evidence=items,
            freshness=freshness(items, catalogue, settings, today),
            internal_audit=recent if internal_audit is ... else internal_audit,
            management_review=recent if management_review is ... else management_review,
            today=today,
        )

    return build
