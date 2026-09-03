"""The finding rules.

Every finding this tool produces comes from one of the rules below, it says
which one, and each rule is a function of the registers alone -- no model, no
heuristic, no judgement that changes between two runs on the same data. That
is not a limitation being apologised for; it is the requirement. A compliance
finding that cannot be reproduced cannot be argued with, and a finding that
cannot be argued with gets ignored.

Severity has a specific meaning here:

``blocking``  the readiness gate refuses while this stands. Reserved for the
              things that fail a certification audit on the day: a mandatory
              clause requirement unmet, an applicable control with no evidence
              at all, a high residual risk carrying nobody's signature.
``high``      would very likely become a major nonconformity.
``medium``    would be raised as a minor nonconformity or an observation.
``low``       hygiene: real, not urgent.
``info``      worth knowing, not wrong.

Two rules deserve their own note, because they are the ones that catch the
failure modes a spreadsheet cannot.

``RISK_TREATED_BY_UNIMPLEMENTED_CONTROL`` is how a register full of green
residual scores turns out to be a plan rather than a state: the risk claims a
reduced score, and the controls it relies on are not implemented. The score
is a forecast, and the tool says so.

``ACCEPTANCE_STALE_AFTER_RESCORING`` is the one nobody writes by hand. A risk
accepted at a residual score of 6 and later re-scored to 15 is not accepted;
it carries a signature for a different number. Every real register drifts this
way, and nothing in a spreadsheet notices.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sentinel.catalogue import Catalogue
from sentinel.config import Settings, get_settings
from sentinel.registers.controls import ControlState, RequirementState
from sentinel.registers.evidence import Evidence
from sentinel.registers.risks import Risk

SEVERITIES = ("blocking", "high", "medium", "low", "info")
_ORDER = {severity: index for index, severity in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class Finding:
    """One thing that is wrong, why it matters, and what to do about it."""

    rule: str
    severity: str
    subject_kind: str
    subject_id: str
    detail: str
    remediation: str

    @property
    def is_blocking(self) -> bool:
        return self.severity == "blocking"

    @property
    def sort_key(self) -> tuple[int, str, str]:
        return (_ORDER[self.severity], self.rule, self.subject_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "detail": self.detail,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class RegisterSnapshot:
    """Everything the rules read. Passed in, so a rule cannot query anything.

    That constraint is what makes the rules testable without a database and
    identical between the CLI, the API and the tests -- and it is why every
    rule below is a pure function of this object.
    """

    catalogue: Catalogue
    control_states: dict[str, ControlState]
    requirement_states: dict[str, RequirementState]
    risks: list[Risk]
    evidence: dict[str, list[Evidence]]
    freshness: dict[str, dict[str, Any]]
    internal_audit: dict[str, Any] | None
    management_review: dict[str, Any] | None
    today: dt.date


# ---------------------------------------------------------------------------
# Control rules
# ---------------------------------------------------------------------------


def control_rules(snapshot: RegisterSnapshot, settings: Settings) -> list[Finding]:
    findings: list[Finding] = []
    for control in snapshot.catalogue.controls:
        state = snapshot.control_states.get(control.id)

        # No decision at all. Not "not implemented" -- nobody has said whether
        # this control applies, and the SoA therefore has a hole in it.
        if state is None:
            findings.append(
                Finding(
                    rule="CONTROL_NOT_ASSESSED",
                    severity="blocking",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(
                        f"{control.id} ({control.title}) has no applicability decision; "
                        "the Statement of Applicability must cover every Annex A control"
                    ),
                    remediation=(
                        "Decide whether the control applies and record the decision, with a "
                        "justification if it does not."
                    ),
                )
            )
            continue

        if not state.applicable:
            # Excluding a control that treats a risk is a contradiction: the
            # organisation has said both that it needs this control and that
            # it does not apply.
            treating = [r for r in snapshot.risks if control.id in r.controls]
            if treating:
                findings.append(
                    Finding(
                        rule="EXCLUDED_CONTROL_TREATS_A_RISK",
                        severity="high",
                        subject_kind="control",
                        subject_id=control.id,
                        detail=(
                            f"{control.id} is excluded from the SoA, yet "
                            f"{len(treating)} risk(s) name it as treatment: "
                            + ", ".join(sorted(r.risk_id for r in treating)[:5])
                        ),
                        remediation=(
                            "Either the control applies after all, or those risks need a "
                            "different treatment. Both statements cannot be true."
                        ),
                    )
                )
            if len((state.justification or "").strip()) < 20:
                findings.append(
                    Finding(
                        rule="EXCLUSION_WEAKLY_JUSTIFIED",
                        severity="medium",
                        subject_kind="control",
                        subject_id=control.id,
                        detail=(
                            f"{control.id} is excluded with a justification of "
                            f"{len((state.justification or '').strip())} characters"
                        ),
                        remediation=(
                            "Write a justification an auditor can assess: what about the "
                            "scope makes this control inapplicable?"
                        ),
                    )
                )
            continue

        # Applicable, but not implemented.
        if state.status != "implemented":
            findings.append(
                Finding(
                    rule="CONTROL_NOT_IMPLEMENTED",
                    severity="blocking" if state.status == "not_started" else "high",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(
                        f"{control.id} ({control.title}) is applicable and its status is "
                        f"{state.status.replace('_', ' ')}"
                    ),
                    remediation=f"Implement the control, or justify excluding it. {control.intent}",
                )
            )
            continue

        # Implemented, with nobody responsible for it.
        if not (state.owner_role or "").strip():
            findings.append(
                Finding(
                    rule="CONTROL_WITHOUT_OWNER",
                    severity="medium",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=f"{control.id} is implemented and has no owning role",
                    remediation=(
                        "Assign a role. A control nobody owns is a control nobody maintains, "
                        "and it will be the one that has decayed at the next audit."
                    ),
                )
            )

        # Implemented, with maturity that contradicts the claim.
        if state.maturity <= 1:
            findings.append(
                Finding(
                    rule="CONTROL_IMPLEMENTED_AT_AD_HOC_MATURITY",
                    severity="low",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(
                        f"{control.id} is recorded as implemented at maturity "
                        f"{state.maturity} ({state.maturity_label})"
                    ),
                    remediation=(
                        "Document how the control operates so that it survives the "
                        "person who currently remembers to do it."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Evidence rules
# ---------------------------------------------------------------------------


def evidence_rules(snapshot: RegisterSnapshot, settings: Settings) -> list[Finding]:
    findings: list[Finding] = []
    for control in snapshot.catalogue.controls:
        state = snapshot.control_states.get(control.id)
        if state is None or not state.is_implemented:
            continue  # covered by the control rules
        status = snapshot.freshness.get(control.id, {})

        if status.get("count", 0) == 0:
            findings.append(
                Finding(
                    rule="CONTROL_WITHOUT_EVIDENCE",
                    severity="blocking",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(f"{control.id} is claimed as implemented with no evidence recorded"),
                    remediation=(
                        "Record at least one artefact: "
                        + ", ".join(control.evidence_expected)
                        + ". An implemented control with no evidence is an assertion."
                    ),
                )
            )
            continue

        if status.get("stale"):
            days = status.get("days_remaining")
            findings.append(
                Finding(
                    rule="EVIDENCE_STALE",
                    severity="high",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(
                        f"{control.id}: the newest evidence was collected on "
                        f"{status.get('newest')} and expired "
                        f"{abs(int(days)) if days is not None else '?'} day(s) ago "
                        f"(valid for {status.get('valid_days')} days)"
                    ),
                    remediation=(
                        "Collect current evidence. Old evidence does not prove the control "
                        "operated since; it proves it operated once."
                    ),
                )
            )
        elif status.get("expiring_soon"):
            findings.append(
                Finding(
                    rule="EVIDENCE_EXPIRING",
                    severity="low",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(
                        f"{control.id}: evidence expires in {status.get('days_remaining')} day(s)"
                    ),
                    remediation="Schedule the collection before it lapses.",
                )
            )

        # Evidence exists, but none of the kind the control calls for. A
        # screenshot is not a substitute for an approved policy.
        kinds = set(status.get("kinds", []))
        expected = set(control.evidence_expected)
        if kinds and expected and not (kinds & expected):
            findings.append(
                Finding(
                    rule="EVIDENCE_KIND_MISMATCH",
                    severity="medium",
                    subject_kind="control",
                    subject_id=control.id,
                    detail=(
                        f"{control.id} is evidenced by {', '.join(sorted(kinds))} "
                        f"but the control calls for {', '.join(sorted(expected))}"
                    ),
                    remediation=(
                        "Collect at least one artefact of an expected kind, or record why "
                        "the substitute is equivalent."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Risk rules
# ---------------------------------------------------------------------------


def risk_rules(snapshot: RegisterSnapshot, settings: Settings) -> list[Finding]:
    findings: list[Finding] = []
    catalogue = snapshot.catalogue

    for risk in snapshot.risks:
        if risk.status == "closed":
            continue

        # A control identifier that the catalogue does not contain. Usually a
        # typo; occasionally a catalogue version change that nobody propagated.
        unknown = [c for c in risk.controls if not catalogue.has_control(c)]
        if unknown:
            findings.append(
                Finding(
                    rule="RISK_NAMES_UNKNOWN_CONTROL",
                    severity="high",
                    subject_kind="risk",
                    subject_id=risk.risk_id,
                    detail=(
                        f"{risk.risk_id} names control(s) absent from catalogue "
                        f"{catalogue.version}: {', '.join(unknown)}"
                    ),
                    remediation=(
                        "Correct the identifier, or map the risk to the control that "
                        "replaced it in this catalogue version."
                    ),
                )
            )

        if risk.treatment == "modify" and not risk.controls:
            findings.append(
                Finding(
                    rule="RISK_MODIFIED_BY_NOTHING",
                    severity="high",
                    subject_kind="risk",
                    subject_id=risk.risk_id,
                    detail=(
                        f"{risk.risk_id} is being treated by modification and names no control"
                    ),
                    remediation=(
                        "Name the controls that modify this risk, or change the treatment "
                        "to retain, avoid or share."
                    ),
                )
            )

        # The important one: a residual score that the implemented controls do
        # not support.
        if risk.residual_score is not None and risk.residual_score < risk.inherent_score:
            not_implemented = [
                control_id
                for control_id in risk.controls
                if catalogue.has_control(control_id)
                and not (
                    snapshot.control_states.get(control_id) or ControlState(control_id)
                ).is_implemented
            ]
            if not_implemented:
                findings.append(
                    Finding(
                        rule="RISK_TREATED_BY_UNIMPLEMENTED_CONTROL",
                        severity="blocking"
                        if risk.effective_score >= settings.risk_escalation
                        else "high",
                        subject_kind="risk",
                        subject_id=risk.risk_id,
                        detail=(
                            f"{risk.risk_id} claims a residual score of {risk.residual_score} "
                            f"(from {risk.inherent_score}) on control(s) that are not "
                            f"implemented: {', '.join(sorted(not_implemented))}"
                        ),
                        remediation=(
                            "Implement the controls, or restore the residual score to the "
                            "inherent one until they are. The current score is a forecast."
                        ),
                    )
                )

        above = risk.above_appetite(settings)
        acceptance = risk.acceptance

        if above and acceptance is None and risk.status != "accepted":
            findings.append(
                Finding(
                    rule="RISK_ABOVE_APPETITE_UNACCEPTED",
                    severity=("blocking" if risk.needs_management_signature(settings) else "high"),
                    subject_kind="risk",
                    subject_id=risk.risk_id,
                    detail=(
                        f"{risk.risk_id} sits at {risk.effective_score} against an appetite "
                        f"of {settings.risk_appetite} with no acceptance on file"
                    ),
                    remediation=(
                        "Treat the risk further, or record a signed acceptance naming who "
                        "accepted it and why."
                    ),
                )
            )

        if acceptance is not None:
            if not acceptance.covers(risk.effective_score):
                findings.append(
                    Finding(
                        rule="ACCEPTANCE_STALE_AFTER_RESCORING",
                        severity="blocking",
                        subject_kind="risk",
                        subject_id=risk.risk_id,
                        detail=(
                            f"{risk.risk_id} was accepted at a residual score of "
                            f"{acceptance.residual_at_acceptance} and now stands at "
                            f"{risk.effective_score}"
                        ),
                        remediation=(
                            "Re-obtain the acceptance against the current score, or treat "
                            "the risk back down to the score that was signed for."
                        ),
                    )
                )
            if acceptance.is_expired(snapshot.today):
                findings.append(
                    Finding(
                        rule="ACCEPTANCE_EXPIRED",
                        severity="high",
                        subject_kind="risk",
                        subject_id=risk.risk_id,
                        detail=(
                            f"{risk.risk_id}: the acceptance expired on {acceptance.expires_on}"
                        ),
                        remediation="Review the risk and renew or withdraw the acceptance.",
                    )
                )
            if (
                risk.needs_management_signature(settings)
                and "top management" not in acceptance.accepted_role.lower()
                and "director" not in acceptance.accepted_role.lower()
            ):
                findings.append(
                    Finding(
                        rule="HIGH_RISK_ACCEPTED_BELOW_MANAGEMENT",
                        severity="high",
                        subject_kind="risk",
                        subject_id=risk.risk_id,
                        detail=(
                            f"{risk.risk_id} at {risk.effective_score} was accepted by "
                            f"{acceptance.accepted_role}, below the escalation level of "
                            f"{settings.risk_escalation}"
                        ),
                        remediation=(
                            "Escalate the acceptance to top management, who own the "
                            "consequences at this level."
                        ),
                    )
                )

        if risk.review_due is not None and risk.review_due < snapshot.today:
            findings.append(
                Finding(
                    rule="RISK_REVIEW_OVERDUE",
                    severity="medium",
                    subject_kind="risk",
                    subject_id=risk.risk_id,
                    detail=f"{risk.risk_id} was due for review on {risk.review_due}",
                    remediation="Review the risk and re-score it, or move the review date deliberately.",
                )
            )

    # A control that treats nothing and is implemented anyway is not wrong --
    # but a *risk-driven* SoA should be able to say why each control is there.
    treated = {control_id for risk in snapshot.risks for control_id in risk.controls}
    orphans = [
        state
        for control_id, state in snapshot.control_states.items()
        if state.applicable
        and catalogue.has_control(control_id)
        and control_id not in treated
        and not (state.justification or "").strip()
    ]
    if orphans:
        findings.append(
            Finding(
                rule="APPLICABLE_CONTROLS_TRACE_TO_NO_RISK",
                severity="info",
                subject_kind="programme",
                subject_id="soa",
                detail=(
                    f"{len(orphans)} applicable control(s) are neither justified in the SoA "
                    "nor named by any risk"
                ),
                remediation=(
                    "Either link them to the risks they treat, or write the justification "
                    "(legal requirement, customer contract, good practice) into the SoA."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Management-system rules
# ---------------------------------------------------------------------------


def management_rules(snapshot: RegisterSnapshot, settings: Settings) -> list[Finding]:
    findings: list[Finding] = []
    for requirement in snapshot.catalogue.requirements:
        state = snapshot.requirement_states.get(requirement.id)
        if state is not None and state.is_met:
            continue
        status = "not recorded" if state is None else state.status.replace("_", " ")
        findings.append(
            Finding(
                rule="CLAUSE_REQUIREMENT_UNMET",
                severity="blocking" if requirement.mandatory_for_certification else "medium",
                subject_kind="requirement",
                subject_id=requirement.id,
                detail=(f"clause {requirement.clause} ({requirement.title}) is {status}"),
                remediation=requirement.intent,
            )
        )

    # Clause 9 wants both of these to have happened, and recently enough to be
    # about the ISMS as it stands rather than as it was.
    for kind, clause, label in (
        ("internal_audit", "9.2", "internal audit"),
        ("management_review", "9.3", "management review"),
    ):
        activity = (
            snapshot.internal_audit if kind == "internal_audit" else snapshot.management_review
        )
        if activity is None:
            findings.append(
                Finding(
                    rule="MANAGEMENT_ACTIVITY_NEVER_PERFORMED",
                    severity="blocking",
                    subject_kind="programme",
                    subject_id=kind,
                    detail=f"no {label} has been recorded (clause {clause})",
                    remediation=(
                        f"Perform and record a {label}. An ISMS with 93 implemented controls "
                        f"and no {label} does not get certified."
                    ),
                )
            )
            continue
        age = (snapshot.today - activity["performed_on"]).days
        if age > 365:
            findings.append(
                Finding(
                    rule="MANAGEMENT_ACTIVITY_OVERDUE",
                    severity="high",
                    subject_kind="programme",
                    subject_id=kind,
                    detail=(f"the last {label} was {age} days ago, on {activity['performed_on']}"),
                    remediation=f"Hold a {label}; the interval should not exceed a year.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Everything, in order
# ---------------------------------------------------------------------------

RULES = (control_rules, evidence_rules, risk_rules, management_rules)


def evaluate(snapshot: RegisterSnapshot, settings: Settings | None = None) -> list[Finding]:
    """Run every rule and return the findings, worst first."""
    settings = settings or get_settings()
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(snapshot, settings))
    return sorted(findings, key=lambda finding: finding.sort_key)


def summarise(findings: Iterable[Finding]) -> dict[str, int]:
    """Counts by severity, with every severity present even at zero.

    Present at zero on purpose: a report whose "blocking" line disappears when
    there are none reads as though the check was not run.
    """
    counts = dict.fromkeys(SEVERITIES, 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts
