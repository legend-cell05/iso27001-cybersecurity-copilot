"""The risk treatment plan, the findings report and the audit dossier.

Three documents with three different readers.

The **risk treatment plan** is read by the people doing the work: what are we
doing about each risk, with which controls, who owns it, and where does that
leave the score. It is ordered by inherent risk, because that is the order the
work should be done in.

The **findings report** is read by whoever has to close the gaps. It is grouped
by rule rather than by control, because "twelve controls have no evidence" is
one task with twelve instances, and a list sorted by control identifier hides
that.

The **audit dossier** is read by an auditor. It is the other two plus the
readiness verdict and the state of clause 9, assembled in the order an audit
actually goes through them.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sentinel.assessment.engine import Assessment
from sentinel.assessment.rules import RegisterSnapshot
from sentinel.config import Settings, get_settings
from sentinel.exports.soa import render_soa
from sentinel.exports.writer import header


def _assessment_block(assessment: Assessment) -> dict[str, str]:
    return {
        "Assessment": str(assessment.assessment_id or "(not saved)"),
        "Date": assessment.run_at.date().isoformat(),
        "Catalogue": f"{assessment.catalogue_version} (`{assessment.catalogue_fingerprint[:16]}`)",
        "Verdict": "ready for audit" if assessment.ready else "not ready",
    }


def render_treatment_plan(
    assessment: Assessment,
    snapshot: RegisterSnapshot,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    risks = sorted(
        snapshot.risks,
        key=lambda risk: (-risk.inherent_score, risk.risk_id),
    )
    posture = assessment.totals["risk"]

    lines = header(
        "Risk treatment plan",
        assessment.organisation,
        assessment.scope,
        _assessment_block(assessment),
    )
    lines += [
        "## Method",
        "",
        "Likelihood and impact are scored 1-5; the product is the risk, so the scale runs",
        f"1-25. The organisation's appetite is **{posture['appetite']}**: at or below it a",
        "residual risk needs no further treatment. Above",
        f"**{posture['escalation']}**, accepting a risk requires top management rather than",
        "a line manager.",
        "",
        "Residual scores count only controls that are **implemented**. A risk whose",
        "reduction rests on a control that is planned is reported as a finding, not as a",
        "lower number.",
        "",
        f"- risks in the register: **{posture['risks']}**",
        f"- carrying a residual score: **{posture['treated']}** ({posture['untreated']} untreated)",
        f"- above appetite: **{posture['above_appetite']}** "
        f"({posture['accepted']} with an acceptance on file)",
        f"- total risk reduced by treatment: **{posture['reduction_pct']}%** "
        f"({posture['inherent_total']} → {posture['effective_total']})",
        "",
        "## The register",
        "",
        "| Risk | Title | Owner | Inherent | Residual | Band | Treatment | Controls | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for risk in risks:
        residual = "—" if risk.residual_score is None else str(risk.residual_score)
        controls = ", ".join(risk.controls) if risk.controls else "*none named*"
        status = risk.status
        if risk.acceptance is not None:
            status = f"accepted ({risk.acceptance.accepted_role})"
        lines.append(
            f"| {risk.risk_id} | {risk.title} | {risk.owner_role} | {risk.inherent_score} | "
            f"{residual} | {risk.band} | {risk.treatment} | {controls} | {status} |"
        )

    above = [risk for risk in risks if risk.above_appetite(settings)]
    lines += [
        "",
        f"## Risks above appetite ({len(above)})",
        "",
    ]
    if not above:
        lines.append("None. Every risk sits at or below the appetite.")
    else:
        lines += [
            "| Risk | Score | Acceptance | Signed by | Covers current score | Expires |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for risk in above:
            acceptance = risk.acceptance
            if acceptance is None:
                lines.append(
                    f"| {risk.risk_id} | {risk.effective_score} | **none on file** | — | — | — |"
                )
                continue
            covers = "yes" if acceptance.covers(risk.effective_score) else "**no**"
            expires = acceptance.expires_on.isoformat() if acceptance.expires_on else "open-ended"
            lines.append(
                f"| {risk.risk_id} | {risk.effective_score} | {acceptance.accepted_on} | "
                f"{acceptance.accepted_role} | {covers} | {expires} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_findings(assessment: Assessment, snapshot: RegisterSnapshot) -> str:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for finding in assessment.findings:
        grouped[finding.rule].append(finding)

    severity_counts = {
        severity: len([f for f in assessment.findings if f.severity == severity])
        for severity in ("blocking", "high", "medium", "low", "info")
    }

    lines = header(
        "Findings",
        assessment.organisation,
        assessment.scope,
        _assessment_block(assessment),
    )
    lines += [
        "## Summary",
        "",
        "| Severity | Count | Meaning |",
        "| --- | --- | --- |",
        f"| blocking | {severity_counts['blocking']} | the readiness gate refuses while this stands |",
        f"| high | {severity_counts['high']} | would very likely become a major nonconformity |",
        f"| medium | {severity_counts['medium']} | a minor nonconformity or an observation |",
        f"| low | {severity_counts['low']} | real, not urgent |",
        f"| info | {severity_counts['info']} | worth knowing, not wrong |",
        "",
        f"{len(assessment.findings)} finding(s) from {len(grouped)} rule(s). Every finding",
        "names the rule that produced it, so a rule the organisation disagrees with can be",
        "argued about rather than the report being ignored wholesale.",
        "",
    ]

    order = {"blocking": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    for rule in sorted(grouped, key=lambda r: (order[grouped[r][0].severity], r)):
        findings = grouped[rule]
        severity = findings[0].severity
        lines += [
            f"## {rule} — {severity} ({len(findings)})",
            "",
            f"*{findings[0].remediation}*",
            "",
            "| Subject | Detail |",
            "| --- | --- |",
        ]
        for finding in findings:
            lines.append(
                f"| {finding.subject_kind} `{finding.subject_id}` | "
                f"{finding.detail.replace('|', '/')} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_dossier(
    assessment: Assessment,
    snapshot: RegisterSnapshot,
    settings: Settings | None = None,
) -> str:
    """Everything an auditor asks for, in the order they ask for it."""
    settings = settings or get_settings()
    totals = assessment.totals
    lines = header(
        "Audit readiness dossier",
        assessment.organisation,
        assessment.scope,
        _assessment_block(assessment),
    )

    lines += [
        "## 1. Verdict",
        "",
    ]
    if assessment.ready and not assessment.forced:
        lines += [
            "The readiness gate **passed**. Every check below was satisfied on the data in",
            "the registers at the time of the assessment. That is a statement about the",
            "records, not a guarantee about an audit: a gate can only check what has been",
            "written down.",
            "",
        ]
    elif assessment.forced:
        lines += [
            "The readiness gate **refused**, and readiness was declared anyway with an",
            f"explicit override. Reason recorded: *{assessment.forced_reason}*",
            "",
        ]
    else:
        lines += [
            f"The readiness gate **refused**: {len(assessment.decision.failed)} check(s) failed",
            f"and {assessment.blocking} blocking finding(s) stand.",
            "",
        ]

    lines += [
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for check in assessment.decision.checks:
        mark = "skipped" if check.skipped else ("pass" if check.passed else "**FAIL**")
        lines.append(f"| {check.name} | {mark} | {check.detail} |")

    lines += [
        "",
        "## 2. Position",
        "",
        "| | |",
        "| --- | --- |",
        f"| Annex A controls | {totals['controls']} |",
        f"| Assessed for applicability | {totals['assessed']} |",
        f"| Applicable | {totals['applicable']} |",
        f"| Excluded | {totals['excluded']} |",
        f"| Implemented | {totals['implemented']} ({totals['implementation_pct']}%) |",
        f"| Implemented and currently evidenced | {totals['evidenced']} ({totals['evidence_pct']}%) |",
        f"| Mean maturity of implemented controls | {totals['mean_maturity']} / 5 |",
        f"| Clause requirements met | {totals['requirements_met']} of {totals['requirements']} |",
        "",
        "### By capability",
        "",
        "| Capability | Applicable | Implemented | |",
        "| --- | --- | --- | --- |",
    ]
    for capability, bucket in sorted(
        totals["by_capability"].items(),
        key=lambda item: (
            item[1]["implemented"] / item[1]["applicable"] if item[1]["applicable"] else 1.0
        ),
    ):
        applicable = bucket["applicable"]
        implemented = bucket["implemented"]
        pct = round(100.0 * implemented / applicable, 1) if applicable else 100.0
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        lines.append(f"| {capability} | {applicable} | {implemented} | `{bar}` {pct}% |")

    lines += [
        "",
        "## 3. Clause 9: audit and review",
        "",
        "| Activity | Last performed | By | Summary |",
        "| --- | --- | --- | --- |",
    ]
    for label, activity in (
        ("Internal audit (9.2)", snapshot.internal_audit),
        ("Management review (9.3)", snapshot.management_review),
    ):
        if activity is None:
            lines.append(f"| {label} | **never** | — | — |")
        else:
            lines.append(
                f"| {label} | {activity['performed_on']} | {activity['performed_by']} | "
                f"{activity['summary']} |"
            )

    lines += [
        "",
        "## 4. Findings",
        "",
        "The full findings report is a separate document; the blocking ones are repeated",
        "here because they are what an audit would open with.",
        "",
    ]
    blocking = [finding for finding in assessment.findings if finding.is_blocking]
    if not blocking:
        lines += ["No blocking findings.", ""]
    else:
        lines += [
            "| Rule | Subject | Detail |",
            "| --- | --- | --- |",
        ]
        for finding in blocking:
            lines.append(
                f"| {finding.rule} | `{finding.subject_id}` | {finding.detail.replace('|', '/')} |"
            )
        lines.append("")

    lines += [
        "## 5. Statement of Applicability",
        "",
        "Reproduced in full below, from the same assessment.",
        "",
        "---",
        "",
    ]
    soa = render_soa(assessment, snapshot, settings)
    # Demote the SoA's headings by one level so the dossier reads as one
    # document rather than two stapled together.
    lines.append(
        "\n".join(f"#{line}" if line.startswith("#") else line for line in soa.split("\n"))
    )
    return "\n".join(lines)
