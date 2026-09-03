"""The readiness gate, and the evidence freshness arithmetic underneath it."""

from __future__ import annotations

import datetime as dt

import pytest

from sentinel.assessment.engine import compute_totals
from sentinel.assessment.gate import evaluate_gate
from sentinel.assessment.rules import evaluate
from sentinel.config import Settings
from sentinel.exceptions import EvidenceError, ReadinessBlocked
from sentinel.registers.evidence import Evidence, freshness, hash_bytes
from tests.conftest import TODAY, make_control_state, make_evidence

SETTINGS = Settings(risk_appetite=6, risk_escalation=15)


def decide(snapshot, settings: Settings = SETTINGS):  # type: ignore[no-untyped-def]
    findings = evaluate(snapshot, settings)
    totals = compute_totals(snapshot, settings)
    return evaluate_gate(snapshot, findings, totals, settings), findings


class TestGate:
    def test_a_complete_isms_passes_every_check(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        decision, _ = decide(snapshot_factory())
        assert decision.ready
        assert len(decision.checks) == 7
        assert all(check.passed for check in decision.checks)

    def test_a_refusal_names_the_check_and_the_number(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        """'Not ready' is useless; '91.4% against a bar of 95%' is a plan."""
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        for control in list(catalogue.controls)[:10]:
            states[control.id] = make_control_state(
                control.id, status="planned", implemented_on=None
            )
        decision, _ = decide(snapshot_factory(control_states=states))
        assert not decision.ready
        check = next(c for c in decision.checks if c.name == "implementation_ratio")
        assert not check.passed
        assert check.value is not None and check.threshold == SETTINGS.min_implemented_ratio
        assert "%" in check.detail

    def test_a_skipped_check_is_not_a_pass(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        """A gate reporting seven passes when it ran six is a gate nobody should trust."""
        settings = Settings(require_internal_audit=False)
        decision, _ = decide(
            snapshot_factory(internal_audit=None, management_review=None), settings
        )
        check = next(c for c in decision.checks if c.name == "internal_audit_and_review")
        assert check.skipped
        assert "says nothing about clause 9" in check.detail

    def test_clause_nine_blocks_when_required(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        decision, _ = decide(snapshot_factory(management_review=None))
        assert not decision.ready
        assert "internal_audit_and_review" in [check.name for check in decision.failed]

    def test_raise_if_blocked_carries_the_failed_checks(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        decision, _ = decide(snapshot_factory(management_review=None))
        with pytest.raises(ReadinessBlocked) as excinfo:
            decision.raise_if_blocked()
        assert "internal_audit_and_review" in excinfo.value.failed

    def test_the_tolerance_for_unevidenced_controls_is_configuration(
        self, snapshot_factory, catalogue
    ) -> None:
        """The same ISMS passes or fails depending on a number the business owns."""
        evidence = {
            control.id: [make_evidence(control.id, kind=control.evidence_expected[0])]
            for control in catalogue.controls
            if control.id != "A.5.30"
        }
        snapshot = snapshot_factory(evidence=evidence)
        strict, _ = decide(snapshot, Settings(max_controls_without_evidence=0))
        lenient, _ = decide(snapshot, Settings(max_controls_without_evidence=1))
        assert not strict.ready
        # Still refused overall -- the blocking finding stands -- but that one
        # check now passes, which is what the setting controls.
        check = next(c for c in lenient.checks if c.name == "implemented_controls_evidenced")
        assert check.passed


class TestTotals:
    def test_excluded_controls_are_not_counted_as_done(self, snapshot_factory, catalogue) -> None:  # type: ignore[no-untyped-def]
        """The most flattering arithmetic error available to a compliance tool."""
        states = {control.id: make_control_state(control.id) for control in catalogue.controls}
        states["A.8.30"] = make_control_state(
            "A.8.30",
            applicable=False,
            status="excluded",
            implemented_on=None,
            justification="No development work is outsourced.",
        )
        totals = compute_totals(snapshot_factory(control_states=states), SETTINGS)
        assert totals["controls"] == 93
        assert totals["applicable"] == 92
        assert totals["excluded"] == 1
        assert totals["implemented"] == 92
        assert totals["implementation_pct"] == 100.0

    def test_capability_buckets_add_up(self, snapshot_factory) -> None:  # type: ignore[no-untyped-def]
        totals = compute_totals(snapshot_factory(), SETTINGS)
        assert sum(b["total"] for b in totals["by_capability"].values()) == 93
        assert sum(b["total"] for b in totals["by_theme"].values()) == 93


class TestFreshness:
    def test_the_newest_artefact_decides(self, catalogue) -> None:  # type: ignore[no-untyped-def]
        """Forty stale artefacts and one fresh one is evidenced; the reverse is not."""
        control = catalogue.control("A.8.7")  # 90-day window
        items = {
            control.id: [
                make_evidence(control.id, age_days=300, kind="screenshot"),
                make_evidence(control.id, age_days=400, kind="report"),
                make_evidence(control.id, age_days=10, kind="screenshot"),
            ]
        }
        status = freshness(items, catalogue, SETTINGS, TODAY)[control.id]
        assert status["count"] == 3
        assert status["newest"] == TODAY - dt.timedelta(days=10)
        assert not status["stale"]

    def test_a_control_with_nothing_on_file(self, catalogue) -> None:  # type: ignore[no-untyped-def]
        status = freshness({}, catalogue, SETTINGS, TODAY)["A.5.1"]
        assert status["count"] == 0
        assert status["stale"] is True
        assert status["newest"] is None

    def test_the_stricter_of_the_two_windows_applies(self, catalogue) -> None:  # type: ignore[no-untyped-def]
        """A global ceiling cannot make a 90-day control last a year."""
        control = catalogue.control("A.8.7")
        items = {control.id: [make_evidence(control.id, age_days=120, kind="screenshot")]}
        settings = Settings(evidence_max_age_days=365)
        assert freshness(items, catalogue, settings, TODAY)[control.id]["stale"]

    def test_expiring_soon_is_a_warning_not_a_failure(self, catalogue) -> None:  # type: ignore[no-untyped-def]
        control = catalogue.control("A.5.1")  # 365-day window
        items = {control.id: [make_evidence(control.id, age_days=330, kind="policy")]}
        status = freshness(items, catalogue, Settings(evidence_warn_days=60), TODAY)[control.id]
        assert not status["stale"]
        assert status["expiring_soon"]
        assert status["days_remaining"] == 35


class TestEvidenceRecord:
    def test_a_hash_that_is_not_one_is_refused(self) -> None:
        """A fake digest looks like proof and is not."""
        with pytest.raises(EvidenceError, match="SHA-256"):
            Evidence(
                subject_id="A.5.1",
                title="Policy",
                kind="policy",
                collected_on=TODAY,
                collected_by="ISO",
                location="x.pdf",
                content_hash="not-a-hash",
            )

    def test_hashing_is_stable(self) -> None:
        assert hash_bytes(b"policy") == hash_bytes(b"policy")
        assert hash_bytes(b"policy") != hash_bytes(b"policy ")

    def test_an_unknown_evidence_kind_is_refused(self) -> None:
        with pytest.raises(EvidenceError, match="unknown evidence kind"):
            Evidence(
                subject_id="A.5.1",
                title="Vibes",
                kind="vibes",
                collected_on=TODAY,
                collected_by="ISO",
                location="x.pdf",
                content_hash="b" * 64,
            )
