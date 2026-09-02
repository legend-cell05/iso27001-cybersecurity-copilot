"""The risk arithmetic and the acceptance semantics.

The interesting assertions here are the refusals: a residual score above its
own inherent score, half a residual pair, an acceptance that no longer covers
the risk it was signed for. Those are the states a spreadsheet reaches
quietly.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sentinel.config import Settings
from sentinel.exceptions import RiskModelError
from sentinel.registers.assets import Asset
from sentinel.registers.controls import ControlState
from sentinel.registers.risks import Acceptance, risk_posture
from tests.conftest import TODAY, make_risk

SETTINGS = Settings(risk_appetite=6, risk_escalation=15)


class TestScoring:
    def test_inherent_is_the_product(self) -> None:
        assert make_risk(likelihood=4, impact=5).inherent_score == 20

    def test_residual_replaces_inherent_once_it_exists(self) -> None:
        risk = make_risk(likelihood=4, impact=5, residual_likelihood=2, residual_impact=3)
        assert risk.inherent_score == 20
        assert risk.residual_score == 6
        assert risk.effective_score == 6

    def test_an_untreated_risk_is_judged_on_its_inherent_score(self) -> None:
        risk = make_risk(likelihood=4, impact=4, residual_likelihood=None, residual_impact=None)
        assert risk.effective_score == 16

    def test_a_treatment_cannot_make_a_risk_worse(self) -> None:
        with pytest.raises(RiskModelError, match="above inherent"):
            make_risk(likelihood=2, impact=2, residual_likelihood=3, residual_impact=3)

    def test_half_a_residual_pair_is_refused(self) -> None:
        with pytest.raises(RiskModelError, match="both factors or neither"):
            make_risk(residual_likelihood=2, residual_impact=None)

    @pytest.mark.parametrize("value", [0, 6, -1, 25])
    def test_scores_outside_the_scale_are_refused(self, value: int) -> None:
        with pytest.raises(RiskModelError, match="outside the"):
            make_risk(likelihood=value)

    @pytest.mark.parametrize(
        ("likelihood", "impact", "band"),
        [(5, 5, "critical"), (4, 4, "critical"), (4, 3, "high"), (3, 2, "medium"), (2, 2, "low")],
    )
    def test_bands(self, likelihood: int, impact: int, band: str) -> None:
        risk = make_risk(
            likelihood=likelihood,
            impact=impact,
            residual_likelihood=likelihood,
            residual_impact=impact,
        )
        assert risk.band == band

    def test_appetite_and_escalation(self) -> None:
        within = make_risk(likelihood=2, impact=3, residual_likelihood=2, residual_impact=3)
        above = make_risk(likelihood=4, impact=3, residual_likelihood=4, residual_impact=3)
        severe = make_risk(likelihood=4, impact=4, residual_likelihood=4, residual_impact=4)
        assert not within.above_appetite(SETTINGS)
        assert above.above_appetite(SETTINGS)
        assert not above.needs_management_signature(SETTINGS)
        assert severe.needs_management_signature(SETTINGS)


class TestAcceptance:
    def _acceptance(self, **overrides: object) -> Acceptance:
        defaults: dict[str, object] = {
            "risk_id": "RSK-001",
            "accepted_by": "H. Rousseau",
            "accepted_role": "Managing Director (top management)",
            "rationale": "Accepted until the migration completes.",
            "residual_at_acceptance": 6,
            "accepted_on": TODAY - dt.timedelta(days=30),
        }
        defaults.update(overrides)
        return Acceptance(**defaults)  # type: ignore[arg-type]

    def test_it_covers_the_score_it_was_signed_for(self) -> None:
        assert self._acceptance().covers(6)
        assert self._acceptance().covers(4)

    def test_it_does_not_cover_a_higher_score(self) -> None:
        """A signature for 6 is not a signature for 15.

        Re-scoring a risk upward silently invalidates its acceptance. Every
        real register drifts this way and nothing in a spreadsheet notices.
        """
        assert not self._acceptance().covers(15)

    def test_an_unscored_risk_is_not_covered(self) -> None:
        assert not self._acceptance().covers(None)

    def test_expiry(self) -> None:
        expired = self._acceptance(expires_on=TODAY - dt.timedelta(days=1))
        current = self._acceptance(expires_on=TODAY + dt.timedelta(days=1))
        open_ended = self._acceptance(expires_on=None)
        assert expired.is_expired(TODAY)
        assert not current.is_expired(TODAY)
        assert not open_ended.is_expired(TODAY)


class TestPosture:
    def test_untreated_risks_are_counted_as_untreated(self) -> None:
        """The number that inflates most easily in a compliance report.

        A treatment *plan* is not a treatment. `treated` counts risks that
        carry a residual score at all.
        """
        risks = [
            make_risk("RSK-001"),
            make_risk("RSK-002", residual_likelihood=None, residual_impact=None),
        ]
        posture = risk_posture(risks, SETTINGS)
        assert posture["risks"] == 2
        assert posture["treated"] == 1
        assert posture["untreated"] == 1

    def test_reduction_is_measured_against_the_inherent_total(self) -> None:
        risks = [
            make_risk("RSK-001", likelihood=4, impact=4, residual_likelihood=2, residual_impact=2)
        ]
        posture = risk_posture(risks, SETTINGS)
        assert posture["inherent_total"] == 16
        assert posture["effective_total"] == 4
        assert posture["reduction_pct"] == 75.0

    def test_an_empty_register_does_not_divide_by_zero(self) -> None:
        assert risk_posture([], SETTINGS)["reduction_pct"] == 0.0


class TestControlState:
    def test_an_exclusion_needs_a_justification(self) -> None:
        with pytest.raises(RiskModelError, match="written justification"):
            ControlState(control_id="A.7.1", applicable=False, status="excluded")

    def test_an_excluded_control_cannot_be_implemented(self) -> None:
        with pytest.raises(RiskModelError, match="must have status 'excluded'"):
            ControlState(
                control_id="A.7.1",
                applicable=False,
                justification="Out of scope: no premises.",
                status="implemented",
                implemented_on=TODAY,
            )

    def test_implemented_is_a_claim_with_a_date(self) -> None:
        with pytest.raises(RiskModelError, match="date on it"):
            ControlState(control_id="A.7.1", status="implemented")

    def test_maturity_is_bounded(self) -> None:
        with pytest.raises(RiskModelError, match="0-5 scale"):
            ControlState(control_id="A.7.1", status="planned", maturity=9)


class TestAsset:
    def test_an_asset_needs_an_owning_role(self) -> None:
        with pytest.raises(RiskModelError, match="no owner"):
            Asset(
                asset_id="AST-001",
                name="Thing",
                kind="service",
                owner_role="  ",
                classification="internal",
                criticality=3,
            )

    def test_unknown_classifications_are_refused(self) -> None:
        with pytest.raises(RiskModelError, match="classification"):
            Asset(
                asset_id="AST-001",
                name="Thing",
                kind="service",
                owner_role="Head of IT",
                classification="top-secret",
                criticality=3,
            )
