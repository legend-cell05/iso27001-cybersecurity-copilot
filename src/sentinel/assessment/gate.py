"""The audit-readiness gate.

A certification audit is not something to walk into hopefully. The gate is the
answer to one question -- *would this ISMS survive a stage 2 audit today?* --
and its job is to be able to say no.

Seven checks, each with the number that produced it and the threshold it was
measured against, because a refusal that cannot be argued with gets overridden
rather than fixed. "Not ready" is useless. "91.4% of applicable controls are
implemented, against a bar of 95%, and these nine are the gap" is a plan.

Two design rules carried over from every gate worth having:

* **a skipped check is recorded as skipped, never as a pass.** A gate that
  reports seven passes when it ran five is a gate nobody should trust;
* **an override is a named flag, and it is recorded.** A gate with no override
  gets routed around by somebody editing the spreadsheet; a gate whose
  override is written onto the assessment keeps the exception inside the
  system, where the auditor can see it too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sentinel.assessment.rules import Finding, RegisterSnapshot
from sentinel.config import Settings, get_settings
from sentinel.exceptions import ReadinessBlocked


@dataclass(frozen=True)
class Check:
    """One gate check: the verdict, the number, and the bar it was held to."""

    name: str
    passed: bool
    detail: str
    value: float | None = None
    threshold: float | None = None
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "detail": self.detail,
            "value": self.value,
            "threshold": self.threshold,
        }


@dataclass
class GateDecision:
    """The verdict, with every check that produced it."""

    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if not check.passed and not check.skipped]

    @property
    def ready(self) -> bool:
        return not self.failed

    @property
    def summary(self) -> str:
        if self.ready:
            ran = len([c for c in self.checks if not c.skipped])
            return f"{ran} check(s) passed, {len(self.checks) - ran} skipped"
        return "; ".join(f"{check.name}: {check.detail}" for check in self.failed)

    def raise_if_blocked(self) -> None:
        if not self.ready:
            raise ReadinessBlocked(self.summary, failed=[check.name for check in self.failed])

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checks": [check.as_dict() for check in self.checks],
            "failed": [check.name for check in self.failed],
        }


def evaluate_gate(
    snapshot: RegisterSnapshot,
    findings: list[Finding],
    totals: dict[str, Any],
    settings: Settings | None = None,
) -> GateDecision:
    """Run the seven checks over an assessment that has already been computed."""
    settings = settings or get_settings()
    decision = GateDecision()
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1

    # 1. Every Annex A control has an applicability decision.
    unassessed = by_rule.get("CONTROL_NOT_ASSESSED", 0)
    total_controls = len(snapshot.catalogue.controls)
    decision.checks.append(
        Check(
            name="every_control_assessed",
            passed=unassessed == 0,
            detail=(
                f"{total_controls - unassessed} of {total_controls} controls carry an "
                "applicability decision" + (f"; {unassessed} do not" if unassessed else "")
            ),
            value=float(total_controls - unassessed),
            threshold=float(total_controls),
        )
    )

    # 2. The mandatory management-system requirements are met.
    mandatory = snapshot.catalogue.mandatory_requirements
    unmet = [
        requirement.id
        for requirement in mandatory
        if not (
            (state := snapshot.requirement_states.get(requirement.id)) is not None and state.is_met
        )
    ]
    decision.checks.append(
        Check(
            name="mandatory_clauses_met",
            passed=not unmet,
            detail=(
                f"{len(mandatory) - len(unmet)} of {len(mandatory)} mandatory clause "
                "requirements are met" + (f"; outstanding: {', '.join(unmet[:6])}" if unmet else "")
            ),
            value=float(len(mandatory) - len(unmet)),
            threshold=float(len(mandatory)),
        )
    )

    # 3. Enough of the applicable controls are implemented.
    applicable = int(totals.get("applicable", 0))
    implemented = int(totals.get("implemented", 0))
    ratio = implemented / applicable if applicable else 0.0
    decision.checks.append(
        Check(
            name="implementation_ratio",
            passed=ratio >= settings.min_implemented_ratio,
            detail=(
                f"{implemented} of {applicable} applicable controls implemented "
                f"({ratio:.1%}), bar {settings.min_implemented_ratio:.0%}"
            ),
            value=round(ratio, 4),
            threshold=settings.min_implemented_ratio,
        )
    )

    # 4. Implemented controls are evidenced.
    without_evidence = by_rule.get("CONTROL_WITHOUT_EVIDENCE", 0)
    decision.checks.append(
        Check(
            name="implemented_controls_evidenced",
            passed=without_evidence <= settings.max_controls_without_evidence,
            detail=(
                f"{without_evidence} implemented control(s) have no evidence, "
                f"tolerance {settings.max_controls_without_evidence}"
            ),
            value=float(without_evidence),
            threshold=float(settings.max_controls_without_evidence),
        )
    )

    # 5. No risk sits above appetite without a signature that covers it.
    uncovered = (
        by_rule.get("RISK_ABOVE_APPETITE_UNACCEPTED", 0)
        + by_rule.get("ACCEPTANCE_STALE_AFTER_RESCORING", 0)
        + by_rule.get("ACCEPTANCE_EXPIRED", 0)
    )
    decision.checks.append(
        Check(
            name="risks_treated_or_accepted",
            passed=uncovered == 0,
            detail=(
                f"{uncovered} risk(s) above the appetite of {settings.risk_appetite} carry "
                "no acceptance that covers their current score"
                if uncovered
                else f"every risk is within appetite ({settings.risk_appetite}) or covered "
                "by an acceptance that matches its current score"
            ),
            value=float(uncovered),
            threshold=0.0,
        )
    )

    # 6. Clause 9 has actually happened. Skippable, visibly, so that the gate
    #    can be run early in a programme without pretending an audit occurred.
    if not settings.require_internal_audit:
        decision.checks.append(
            Check(
                name="internal_audit_and_review",
                passed=True,
                skipped=True,
                detail=(
                    "not required by configuration -- SENTINEL_REQUIRE_INTERNAL_AUDIT is off, "
                    "so this says nothing about clause 9"
                ),
            )
        )
    else:
        missing = by_rule.get("MANAGEMENT_ACTIVITY_NEVER_PERFORMED", 0)
        overdue = by_rule.get("MANAGEMENT_ACTIVITY_OVERDUE", 0)
        audit = snapshot.internal_audit
        review = snapshot.management_review
        detail_parts = []
        detail_parts.append(
            f"internal audit {audit['performed_on']}" if audit else "no internal audit"
        )
        detail_parts.append(
            f"management review {review['performed_on']}" if review else "no management review"
        )
        decision.checks.append(
            Check(
                name="internal_audit_and_review",
                passed=missing == 0 and overdue == 0,
                detail="; ".join(detail_parts),
                value=float(missing + overdue),
                threshold=0.0,
            )
        )

    # 7. The aggregate. Kept last and kept separate: the six checks above are
    #    the ones a security manager can act on, and this one is the catch-all
    #    for a blocking finding that no individual check happens to cover.
    blocking = len([finding for finding in findings if finding.is_blocking])
    decision.checks.append(
        Check(
            name="no_blocking_findings",
            passed=blocking == 0,
            detail=(
                f"{blocking} blocking finding(s) across "
                f"{len({finding.rule for finding in findings if finding.is_blocking})} rule(s)"
                if blocking
                else "no blocking findings"
            ),
            value=float(blocking),
            threshold=0.0,
        )
    )
    return decision
