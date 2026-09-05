"""One test per finding rule: it fires when it should, and stays quiet otherwise.

Every test starts from a snapshot that produces no findings at all and breaks
exactly one thing, so a finding in the result is unambiguously caused by what
the test changed. The first assertion of the file is therefore the most
important one: that the clean snapshot really is clean.

No database is touched here. The rules read a :class:`RegisterSnapshot` that is
passed in, which is what makes that possible -- and the reason the rules were
written that way.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sentinel.assessment.rules import evaluate, summarise
from sentinel.config import Settings
from sentinel.registers.controls import RequirementState
from sentinel.registers.risks import Acceptance
from tests.conftest import TODAY, make_control_state, make_evidence, make_risk

SETTINGS = Settings(risk_appetite=6, risk_escalation=15)


def rules_fired(findings: list) -> set[str]:  # type: ignore[type-arg]
    return {finding.rule for finding in findings}


class TestCleanBaseline:
    def test_a_complete_isms_produces_no_findings(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        """The assertion every other test in this file depends on."""
        findings = evaluate(snapshot_factory(), SETTINGS)
        assert findings == [], [finding.rule for finding in findings]

    def test_severities_are_always_reported_even_at_zero(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        """A report whose 'blocking' line vanishes reads as though nothing ran."""
        counts = summarise(evaluate(snapshot_factory(), SETTINGS))
        assert set(counts) == {"blocking", "high", "medium", "low", "info"}
        assert counts["blocking"] == 0


class TestControlRules:
    def test_a_control_with_no_decision_is_blocking(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        states = {
            control.id: make_control_state(control.id)
            for control in catalogue.controls
            if control.id != "A.5.7"
        }
        findings = evaluate(snapshot_factory(control_states=states), SETTINGS)
        assert rules_fired(findings) == {"CONTROL_NOT_ASSESSED"}
        assert findings[0].is_blocking

    def test_not_started_blocks_and_planned_does_not(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        """Both are gaps; only one of them fails an audit on the day."""
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.5.1"] = make_control_state("A.5.1", status="not_started", implemented_on=None)
        states["A.5.2"] = make_control_state("A.5.2", status="planned", implemented_on=None)
        findings = evaluate(snapshot_factory(control_states=states), SETTINGS)
        by_subject = {finding.subject_id: finding for finding in findings}
        assert by_subject["A.5.1"].severity == "blocking"
        assert by_subject["A.5.2"].severity == "high"

    def test_an_exclusion_that_treats_a_risk_is_a_contradiction(
        self, snapshot_factory, catalogue
    ) -> None:  # type: ignore[no-untyped-def]
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.8.22"] = make_control_state(
            "A.8.22",
            applicable=False,
            status="excluded",
            implemented_on=None,
            justification="The depot and corporate networks are treated as one zone.",
        )
        risks = [make_risk("RSK-009", controls=("A.8.22",))]
        findings = evaluate(snapshot_factory(control_states=states, risks=risks), SETTINGS)
        assert "EXCLUDED_CONTROL_TREATS_A_RISK" in rules_fired(findings)

    def test_a_one_line_exclusion_is_flagged(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.5.6"] = make_control_state(
            "A.5.6",
            applicable=False,
            status="excluded",
            implemented_on=None,
            justification="Not needed.",
        )
        findings = evaluate(snapshot_factory(control_states=states), SETTINGS)
        assert "EXCLUSION_WEAKLY_JUSTIFIED" in rules_fired(findings)

    def test_an_implemented_control_with_no_owner(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.8.6"] = make_control_state("A.8.6", owner_role=None)
        findings = evaluate(snapshot_factory(control_states=states), SETTINGS)
        assert rules_fired(findings) == {"CONTROL_WITHOUT_OWNER"}


class TestEvidenceRules:
    def test_an_implemented_control_with_no_evidence_is_blocking(
        self, snapshot_factory, catalogue
    ) -> None:  # type: ignore[no-untyped-def]
        evidence = {
            control.id: [make_evidence(control.id, kind=control.evidence_expected[0])]
            for control in catalogue.controls
            if control.id != "A.5.30"
        }
        findings = evaluate(snapshot_factory(evidence=evidence), SETTINGS)
        assert rules_fired(findings) == {"CONTROL_WITHOUT_EVIDENCE"}
        assert findings[0].is_blocking

    def test_evidence_past_its_window_is_stale(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        """A.8.7 keeps evidence for 90 days; this is 200 days old."""
        evidence = {
            control.id: [
                make_evidence(
                    control.id,
                    kind=control.evidence_expected[0],
                    age_days=200 if control.id == "A.8.7" else 10,
                )
            ]
            for control in catalogue.controls
        }
        findings = evaluate(snapshot_factory(evidence=evidence), SETTINGS)
        assert "EVIDENCE_STALE" in rules_fired(findings)
        assert [f.subject_id for f in findings if f.rule == "EVIDENCE_STALE"] == ["A.8.7"]

    def test_evidence_within_the_window_is_not_stale(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        evidence = {
            control.id: [
                make_evidence(
                    control.id,
                    kind=control.evidence_expected[0],
                    age_days=20 if control.id == "A.8.7" else 10,
                )
            ]
            for control in catalogue.controls
        }
        assert evaluate(snapshot_factory(evidence=evidence), SETTINGS) == []

    def test_the_wrong_kind_of_artefact_is_reported(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        """A screenshot is not a substitute for an approved policy."""
        evidence = {
            control.id: [
                make_evidence(
                    control.id,
                    kind="screenshot" if control.id == "A.5.1" else control.evidence_expected[0],
                )
            ]
            for control in catalogue.controls
        }
        findings = evaluate(snapshot_factory(evidence=evidence), SETTINGS)
        assert rules_fired(findings) == {"EVIDENCE_KIND_MISMATCH"}


class TestRiskRules:
    def test_a_residual_score_resting_on_an_unimplemented_control(
        self, snapshot_factory, catalogue
    ) -> None:  # type: ignore[no-untyped-def]
        """The rule that catches a treatment plan reported as a treatment."""
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.8.12"] = make_control_state("A.8.12", status="planned", implemented_on=None)
        risks = [
            make_risk(
                "RSK-026",
                likelihood=2,
                impact=5,
                residual_likelihood=1,
                residual_impact=3,
                controls=("A.8.12",),
            )
        ]
        findings = evaluate(snapshot_factory(control_states=states, risks=risks), SETTINGS)
        assert "RISK_TREATED_BY_UNIMPLEMENTED_CONTROL" in rules_fired(findings)

    def test_the_same_risk_is_clean_once_the_control_is_implemented(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [
            make_risk(
                "RSK-026",
                likelihood=2,
                impact=5,
                residual_likelihood=1,
                residual_impact=3,
                controls=("A.8.12",),
            )
        ]
        assert evaluate(snapshot_factory(risks=risks), SETTINGS) == []

    def test_an_unknown_control_identifier(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [make_risk("RSK-011", controls=("A.5.15", "A.5.99"))]
        findings = evaluate(snapshot_factory(risks=risks), SETTINGS)
        assert "RISK_NAMES_UNKNOWN_CONTROL" in rules_fired(findings)

    def test_modify_with_no_control_named(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [make_risk("RSK-027", treatment="modify", controls=())]
        findings = evaluate(snapshot_factory(risks=risks), SETTINGS)
        assert "RISK_MODIFIED_BY_NOTHING" in rules_fired(findings)

    def test_above_appetite_with_nothing_signed(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [
            make_risk(
                "RSK-016",
                likelihood=4,
                impact=4,
                residual_likelihood=None,
                residual_impact=None,
                status="open",
                controls=("A.8.15",),
            )
        ]
        findings = evaluate(snapshot_factory(risks=risks), SETTINGS)
        finding = next(f for f in findings if f.rule == "RISK_ABOVE_APPETITE_UNACCEPTED")
        assert finding.is_blocking  # 16 is at or above the escalation level

    def test_an_acceptance_signed_for_a_smaller_number_is_blocking(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        """The finding nobody writes by hand, and every register needs."""
        risks = [
            make_risk(
                "RSK-014",
                likelihood=3,
                impact=4,
                residual_likelihood=2,
                residual_impact=4,
                status="accepted",
                controls=("A.7.11",),
                acceptance=Acceptance(
                    risk_id="RSK-014",
                    accepted_by="C. Fontaine",
                    accepted_role="Operations Director",
                    rationale="Accepted until the UPS is replaced.",
                    residual_at_acceptance=4,
                    accepted_on=TODAY - dt.timedelta(days=210),
                ),
            )
        ]
        findings = evaluate(snapshot_factory(risks=risks), SETTINGS)
        finding = next(f for f in findings if f.rule == "ACCEPTANCE_STALE_AFTER_RESCORING")
        assert finding.is_blocking
        assert "4" in finding.detail and "8" in finding.detail

    def test_an_expired_acceptance(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [
            make_risk(
                "RSK-003",
                likelihood=3,
                impact=5,
                residual_likelihood=2,
                residual_impact=4,
                status="accepted",
                controls=("A.8.13",),
                acceptance=Acceptance(
                    risk_id="RSK-003",
                    accepted_by="M. Delaunay",
                    accepted_role="Managing Director (top management)",
                    rationale="Accepted for one quarter.",
                    residual_at_acceptance=8,
                    accepted_on=TODAY - dt.timedelta(days=300),
                    expires_on=TODAY - dt.timedelta(days=30),
                ),
            )
        ]
        assert "ACCEPTANCE_EXPIRED" in rules_fired(
            evaluate(snapshot_factory(risks=risks), SETTINGS)
        )

    def test_a_severe_risk_accepted_by_a_manager(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [
            make_risk(
                "RSK-023",
                likelihood=4,
                impact=4,
                residual_likelihood=4,
                residual_impact=4,
                status="accepted",
                controls=("A.8.8",),
                acceptance=Acceptance(
                    risk_id="RSK-023",
                    accepted_by="M. Delaunay",
                    accepted_role="Head of IT",
                    rationale="Accepted until scanning is procured.",
                    residual_at_acceptance=16,
                    accepted_on=TODAY - dt.timedelta(days=60),
                ),
            )
        ]
        findings = evaluate(snapshot_factory(risks=risks), SETTINGS)
        assert "HIGH_RISK_ACCEPTED_BELOW_MANAGEMENT" in rules_fired(findings)

    def test_top_management_satisfies_the_same_case(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        risks = [
            make_risk(
                "RSK-023",
                likelihood=4,
                impact=4,
                residual_likelihood=4,
                residual_impact=4,
                status="accepted",
                controls=("A.8.8",),
                acceptance=Acceptance(
                    risk_id="RSK-023",
                    accepted_by="H. Rousseau",
                    accepted_role="Managing Director (top management)",
                    rationale="Accepted until scanning is procured.",
                    residual_at_acceptance=16,
                    accepted_on=TODAY - dt.timedelta(days=60),
                ),
            )
        ]
        assert evaluate(snapshot_factory(risks=risks), SETTINGS) == []


class TestManagementRules:
    def test_an_unmet_mandatory_clause_is_blocking(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        requirements = {
            requirement.id: RequirementState(
                requirement_id=requirement.id,
                status="met",
                completed_on=TODAY - dt.timedelta(days=30),
            )
            for requirement in catalogue.requirements
        }
        requirements["C.9.3"] = RequirementState(requirement_id="C.9.3", status="not_started")
        findings = evaluate(snapshot_factory(requirement_states=requirements), SETTINGS)
        finding = next(f for f in findings if f.rule == "CLAUSE_REQUIREMENT_UNMET")
        assert finding.is_blocking
        assert finding.subject_id == "C.9.3"

    def test_a_missing_management_review_is_blocking(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        findings = evaluate(snapshot_factory(management_review=None), SETTINGS)
        finding = next(f for f in findings if f.rule == "MANAGEMENT_ACTIVITY_NEVER_PERFORMED")
        assert finding.is_blocking
        assert finding.subject_id == "management_review"

    def test_an_audit_older_than_a_year(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        stale = {"performed_on": TODAY - dt.timedelta(days=430), "performed_by": "auditor"}
        findings = evaluate(snapshot_factory(internal_audit=stale), SETTINGS)
        assert "MANAGEMENT_ACTIVITY_OVERDUE" in rules_fired(findings)


class TestOrdering:
    def test_findings_come_back_worst_first(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.5.1"] = make_control_state("A.5.1", status="not_started", implemented_on=None)
        states["A.8.6"] = make_control_state("A.8.6", owner_role=None)
        states["A.5.37"] = make_control_state("A.5.37", maturity=1)
        findings = evaluate(snapshot_factory(control_states=states), SETTINGS)
        severities = [finding.severity for finding in findings]
        assert severities == sorted(
            severities, key=lambda s: ["blocking", "high", "medium", "low", "info"].index(s)
        )


@pytest.mark.parametrize("rule_name", ["CONTROL_WITHOUT_EVIDENCE", "CLAUSE_REQUIREMENT_UNMET"])
def test_every_finding_says_what_to_do(snapshot_factory, catalogue, rule_name: str) -> None:  # type: ignore[no-untyped-def]
    """A finding with no remediation is a complaint."""
    requirements = {
        requirement.id: RequirementState(requirement_id=requirement.id, status="not_started")
        for requirement in catalogue.requirements
    }
    findings = evaluate(snapshot_factory(requirement_states=requirements, evidence={}), SETTINGS)
    for finding in findings:
        assert finding.remediation.strip()
        assert finding.detail.strip()
    assert rule_name in rules_fired(findings)
