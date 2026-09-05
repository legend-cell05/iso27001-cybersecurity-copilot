"""The platform end to end, against a real PostgreSQL.

What is worth testing here is what a mock would simply agree with: the CHECK
constraints that refuse an impossible register, the partial unique index that
allows one acceptance in force per risk, the reproducibility of an export, and
the two demo profiles behaving as documented.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sentinel import pipeline
from sentinel.assessment.engine import build_snapshot, latest_assessment, run_assessment
from sentinel.catalogue import get_catalogue
from sentinel.config import Settings
from sentinel.db.engine import get_engine
from sentinel.db.schema import initialise_database, table_counts
from sentinel.demo import PLANTED, TODAY, generate
from sentinel.exceptions import ReadinessBlocked
from sentinel.exports.soa import render_soa, render_soa_csv
from sentinel.registers.controls import load_control_states
from sentinel.registers.risks import Acceptance, accept_risk, list_risks
from tests.conftest import requires_database

pytestmark = [requires_database, pytest.mark.integration]


@pytest.fixture
def gaps(integration_settings: Settings) -> Settings:
    """The mid-programme organisation, regenerated for each test that wants it."""
    _reset(integration_settings)
    generate("gaps", integration_settings)
    return integration_settings


@pytest.fixture
def ready(integration_settings: Settings) -> Settings:
    _reset(integration_settings)
    generate("ready", integration_settings)
    return integration_settings


def _reset(settings: Settings) -> None:
    schema = settings.schema_name
    with get_engine(settings).begin() as conn:
        for table in (
            "export",
            "readiness_check",
            "finding",
            "assessment",
            "evidence",
            "management_activity",
            "risk_acceptance",
            "risk_control",
            "risk",
            "asset",
            "control_state",
            "requirement_state",
        ):
            conn.execute(text(f"TRUNCATE {schema}.{table} CASCADE"))


class TestSchema:
    def test_initialisation_is_safe_to_repeat(self, integration_settings: Settings) -> None:
        """A compliance tool that empties its own evidence index on start-up
        destroys the only record that the organisation ever did the work."""
        generate("ready", integration_settings)
        before = table_counts(integration_settings)
        initialise_database(integration_settings)
        assert table_counts(integration_settings) == before
        assert before["evidence"] > 0

    def test_a_residual_score_above_its_inherent_one_is_refused(
        self, integration_settings: Settings
    ) -> None:
        """Enforced by the database, not by the application that could be bypassed."""
        schema = integration_settings.schema_name
        with pytest.raises(IntegrityError), get_engine(integration_settings).begin() as conn:
            conn.execute(
                text(
                    f"""INSERT INTO {schema}.risk
                        (risk_id, title, threat, vulnerability, owner_role,
                         likelihood, impact, residual_likelihood, residual_impact)
                        VALUES ('RSK-BAD', 't', 'th', 'v', 'Head of IT', 2, 2, 4, 4)"""
                )
            )

    def test_one_acceptance_in_force_per_risk(self, gaps: Settings) -> None:
        """Enforced by a partial unique index; `accept_risk` withdraws the previous one."""
        risk = next(r for r in list_risks(gaps) if r.acceptance is not None)
        accept_risk(
            Acceptance(
                risk_id=risk.risk_id,
                accepted_by="H. Rousseau",
                accepted_role="Managing Director (top management)",
                rationale="Re-signed against the current score.",
                residual_at_acceptance=risk.effective_score,
                accepted_on=TODAY,
            ),
            gaps,
        )
        schema = gaps.schema_name
        with get_engine(gaps).connect() as conn:
            in_force = conn.execute(
                text(
                    f"""SELECT COUNT(*) FROM {schema}.risk_acceptance
                        WHERE risk_id = :risk_id AND withdrawn_on IS NULL"""
                ),
                {"risk_id": risk.risk_id},
            ).scalar_one()
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM {schema}.risk_acceptance WHERE risk_id = :risk_id"),
                {"risk_id": risk.risk_id},
            ).scalar_one()
        assert in_force == 1
        # The superseded one is kept: "who accepted this, when, and who changed
        # their mind" is a question an auditor does ask.
        assert total == 2

    def test_an_exclusion_without_a_reason_is_refused(self, integration_settings: Settings) -> None:
        schema = integration_settings.schema_name
        with pytest.raises(IntegrityError), get_engine(integration_settings).begin() as conn:
            conn.execute(
                text(
                    f"""INSERT INTO {schema}.control_state
                        (control_id, applicable, status) VALUES ('A.7.1', FALSE, 'excluded')"""
                )
            )


class TestGapsProfile:
    def test_the_gate_refuses(self, gaps: Settings) -> None:
        assessment = run_assessment(gaps, today=TODAY)
        assert not assessment.decision.ready
        assert assessment.blocking > 0
        assert len(assessment.decision.failed) == 7

    def test_every_planted_defect_is_found(self, gaps: Settings) -> None:
        """The demo plants defects on purpose; the engine must find each kind."""
        assessment = run_assessment(gaps, today=TODAY)
        fired = {finding.rule for finding in assessment.findings}
        for rule in (
            "CONTROL_NOT_ASSESSED",
            "CONTROL_NOT_IMPLEMENTED",
            "CONTROL_WITHOUT_EVIDENCE",
            "CONTROL_WITHOUT_OWNER",
            "CONTROL_IMPLEMENTED_AT_AD_HOC_MATURITY",
            "EVIDENCE_STALE",
            "EVIDENCE_KIND_MISMATCH",
            "EXCLUSION_WEAKLY_JUSTIFIED",
            "EXCLUDED_CONTROL_TREATS_A_RISK",
            "RISK_NAMES_UNKNOWN_CONTROL",
            "RISK_MODIFIED_BY_NOTHING",
            "RISK_TREATED_BY_UNIMPLEMENTED_CONTROL",
            "RISK_ABOVE_APPETITE_UNACCEPTED",
            "ACCEPTANCE_STALE_AFTER_RESCORING",
            "ACCEPTANCE_EXPIRED",
            "HIGH_RISK_ACCEPTED_BELOW_MANAGEMENT",
            "CLAUSE_REQUIREMENT_UNMET",
            "MANAGEMENT_ACTIVITY_NEVER_PERFORMED",
            "MANAGEMENT_ACTIVITY_OVERDUE",
        ):
            assert rule in fired, f"{rule} was planted and not found"
        assert len(PLANTED) >= 20

    def test_declaring_readiness_is_refused(self, gaps: Settings) -> None:
        assessment = run_assessment(gaps, today=TODAY)
        with pytest.raises(ReadinessBlocked):
            pipeline.declare_ready(assessment)

    def test_forcing_needs_a_reason_and_records_it(self, gaps: Settings) -> None:
        assessment = run_assessment(gaps, today=TODAY)
        with pytest.raises(ReadinessBlocked, match="needs a reason"):
            pipeline.declare_ready(assessment, force=True, reason="   ")
        pipeline.declare_ready(
            assessment, force=True, reason="Stage 1 booked; the board accepted the gaps."
        )
        assert assessment.forced and assessment.ready


class TestReadyProfile:
    def test_the_gate_passes(self, ready: Settings) -> None:
        """A gate that has never passed is as untrustworthy as one that never refuses."""
        assessment = run_assessment(ready, today=TODAY)
        assert assessment.decision.ready, assessment.decision.summary
        assert assessment.blocking == 0
        assert all(check.passed for check in assessment.decision.checks)

    def test_every_control_carries_a_decision(self, ready: Settings) -> None:
        catalogue = get_catalogue(ready)
        states = load_control_states(ready)
        assert set(states) == set(catalogue.control_ids)


class TestAssessmentRecord:
    def test_an_assessment_is_kept_with_its_findings_and_checks(self, gaps: Settings) -> None:
        assessment = run_assessment(gaps, today=TODAY)
        stored = latest_assessment(gaps)
        assert stored is not None
        assert stored["assessment_id"] == assessment.assessment_id
        assert len(stored["findings"]) == len(assessment.findings)
        assert len(stored["checks"]) == 7
        # The fingerprint is what lets a six-month-old SoA be matched to the
        # catalogue that produced it.
        assert stored["catalogue_fingerprint"] == get_catalogue(gaps).fingerprint

    def test_the_thresholds_in_force_are_frozen_onto_the_run(self, gaps: Settings) -> None:
        """'Ready' under a 50% bar is not the same claim as under 95%."""
        run_assessment(gaps, today=TODAY)
        stored = latest_assessment(gaps)
        assert stored is not None
        assert stored["parameters"]["min_implemented_ratio"] == gaps.min_implemented_ratio
        assert stored["parameters"]["risk_appetite"] == gaps.risk_appetite


class TestExports:
    def test_the_soa_covers_every_annex_a_control(self, gaps: Settings) -> None:
        catalogue = get_catalogue(gaps)
        assessment = run_assessment(gaps, today=TODAY)
        snapshot = build_snapshot(gaps, catalogue, TODAY)
        document = render_soa(assessment, snapshot, gaps)
        for control in catalogue.controls:
            assert f"| {control.id} |" in document, f"{control.id} missing from the SoA"

    def test_an_undecided_control_appears_as_a_gap(self, gaps: Settings) -> None:
        """Built by walking the catalogue, not by listing the rows somebody filled in."""
        assessment = run_assessment(gaps, today=TODAY)
        snapshot = build_snapshot(gaps, get_catalogue(gaps), TODAY)
        document = render_soa(assessment, snapshot, gaps)
        row = next(line for line in document.splitlines() if line.startswith("| A.5.7 |"))
        assert "not decided" in row

    def test_the_same_assessment_produces_the_same_document(self, gaps: Settings) -> None:
        """Two exports that differ mean the ISMS differed, not the tool."""
        assessment = run_assessment(gaps, today=TODAY)
        snapshot = build_snapshot(gaps, get_catalogue(gaps), TODAY)
        first = render_soa(assessment, snapshot, gaps)
        second = render_soa(assessment, build_snapshot(gaps, get_catalogue(gaps), TODAY), gaps)
        assert first == second
        assert render_soa_csv(assessment, snapshot).count("\n") == 94  # header + 93

    def test_exports_are_recorded_with_their_hash(self, gaps: Settings) -> None:
        assessment = run_assessment(gaps, today=TODAY)
        pipeline.export_all(assessment, gaps, today=TODAY)
        schema = gaps.schema_name
        with get_engine(gaps).connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""SELECT kind, content_hash FROM {schema}.export
                            WHERE assessment_id = :id"""
                    ),
                    {"id": assessment.assessment_id},
                )
                .mappings()
                .all()
            )
        kinds = {row["kind"] for row in rows}
        assert kinds == {"soa", "risk_treatment_plan", "findings", "audit_dossier"}
        assert all(len(row["content_hash"]) == 64 for row in rows)


class TestApi:
    def test_readiness_answers_409_when_the_gate_refuses(self, gaps: Settings) -> None:
        """A monitoring check should be able to be a URL rather than a parser."""
        from sentinel.api.app import create_app

        with TestClient(create_app(gaps)) as client:
            response = client.get("/api/v1/readiness")
        assert response.status_code == 409
        assert response.json()["ready"] is False

    def test_readiness_answers_200_once_the_gaps_are_closed(self, ready: Settings) -> None:
        from sentinel.api.app import create_app

        with TestClient(create_app(ready)) as client:
            response = client.get("/api/v1/readiness")
            posture = client.get("/api/v1/posture").json()
        assert response.status_code == 200
        assert posture["controls"]["implementation_pct"] == 100.0
        assert posture["risk"]["above_appetite"] >= 0

    def test_the_catalogue_endpoint_states_the_copyright_position(
        self, integration_settings: Settings
    ) -> None:
        from sentinel.api.app import create_app

        with TestClient(create_app(integration_settings)) as client:
            body = client.get("/api/v1/catalogue").json()
        assert body["controls"] == 93
        assert "No text from the standard is reproduced" in body["note"]


class TestEvidenceFreshnessOverTime:
    def test_the_same_registers_age_into_a_finding(self, ready: Settings) -> None:
        """Nothing changes but the calendar, and the ISMS stops being ready.

        This is the property that makes evidence freshness worth modelling: an
        organisation does not decay by doing something wrong, it decays by
        doing nothing for long enough.
        """
        today = run_assessment(ready, today=TODAY, persist=False)
        assert today.decision.ready

        later = run_assessment(ready, today=TODAY + dt.timedelta(days=400), persist=False)
        assert not later.decision.ready
        fired = {finding.rule for finding in later.findings}
        assert "EVIDENCE_STALE" in fired
        assert "MANAGEMENT_ACTIVITY_OVERDUE" in fired
