"""The Statement of Applicability.

The document a certification audit opens first, and the one most often sent
back. It has to say, for all 93 Annex A controls: does this one apply, why,
is it implemented, and what is the evidence.

Two things this generator does that a spreadsheet does not.

**It cannot silently omit a control.** The document is built by walking the
catalogue, not by listing the rows somebody remembered to fill in. A control
with no decision appears in the SoA as an explicit gap, in the table, where an
auditor will see it -- which is uncomfortable and correct.

**The justification for an included control is derived where possible.** A
control that treats named risks is included *because* it treats them, and the
risk identifiers are printed. An SoA whose justification column reads "best
practice" ninety-three times is a document nobody believes.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sentinel.assessment.engine import Assessment
from sentinel.assessment.rules import RegisterSnapshot
from sentinel.config import Settings, get_settings
from sentinel.exports.writer import header

_STATUS_LABEL = {
    "not_started": "Not started",
    "planned": "Planned",
    "in_progress": "In progress",
    "implemented": "Implemented",
    "excluded": "Excluded",
}


def _risks_treated_by(snapshot: RegisterSnapshot) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for risk in snapshot.risks:
        for control_id in risk.controls:
            mapping.setdefault(control_id, []).append(risk.risk_id)
    return {key: sorted(value) for key, value in mapping.items()}


def _justification(
    control_id: str,
    state: Any,
    treated: dict[str, list[str]],
) -> str:
    """Why this control is in or out, in a form an auditor can assess."""
    if state is None:
        return (
            "**No decision recorded.** This control has not been assessed for "
            "applicability; the Statement of Applicability is incomplete until it is."
        )
    if not state.applicable:
        return (state.justification or "").strip() or "*(no justification recorded)*"

    written = (state.justification or "").strip()
    risks = treated.get(control_id, [])
    if risks and written:
        return f"{written} Treats {', '.join(risks)}."
    if risks:
        return f"Selected to treat {', '.join(risks)}."
    if written:
        return written
    return (
        "Included as part of the baseline; no risk in the register names it and no "
        "justification has been written."
    )


def render_soa(
    assessment: Assessment,
    snapshot: RegisterSnapshot,
    settings: Settings | None = None,
) -> str:
    """The SoA as Markdown."""
    settings = settings or get_settings()
    catalogue = snapshot.catalogue
    treated = _risks_treated_by(snapshot)
    totals = assessment.totals

    lines = header(
        "Statement of Applicability",
        assessment.organisation,
        assessment.scope,
        {
            "Assessment": str(assessment.assessment_id or "(not saved)"),
            "Date": assessment.run_at.date().isoformat(),
            "Catalogue": f"{assessment.catalogue_version} (`{assessment.catalogue_fingerprint[:16]}`)",
            "Controls": (
                f"{totals['applicable']} applicable, {totals['excluded']} excluded, "
                f"{totals['implemented']} implemented"
            ),
        },
    )

    lines += [
        "## How to read this document",
        "",
        "One row per Annex A control. **Applicable** is the organisation's decision;",
        "**justification** is why, and for an excluded control it is the only thing",
        "standing between the organisation and a nonconformity. **Evidence** counts the",
        "current artefacts on file -- current meaning within the freshness window for",
        "that control, not merely present.",
        "",
        f"*{catalogue.framework_note.strip()}*",
        "",
    ]

    for theme, theme_description in catalogue.themes.items():
        controls = catalogue.by_theme(theme)
        if not controls:
            continue
        bucket = totals["by_theme"].get(theme, {})
        lines += [
            f"## {theme.capitalize()} controls ({len(controls)})",
            "",
            f"*{theme_description}* — "
            f"{bucket.get('implemented', 0)} of {bucket.get('applicable', 0)} applicable "
            "implemented.",
            "",
            "| Control | Title | Applicable | Status | Evidence | Justification |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for control in controls:
            state = snapshot.control_states.get(control.id)
            fresh = snapshot.freshness.get(control.id, {})
            if state is None:
                applicable = "**not decided**"
                status = "—"
            else:
                applicable = "Yes" if state.applicable else "No"
                status = _STATUS_LABEL[state.status]
            count = int(fresh.get("count", 0))
            if count == 0:
                evidence = "none"
            elif fresh.get("stale"):
                evidence = f"{count} (stale)"
            else:
                evidence = f"{count}, newest {fresh.get('newest')}"
            justification = _justification(control.id, state, treated).replace("\n", " ")
            lines.append(
                f"| {control.id} | {control.title} | {applicable} | {status} | "
                f"{evidence} | {justification} |"
            )
        lines.append("")

    excluded = [
        (control_id, state)
        for control_id, state in sorted(snapshot.control_states.items())
        if not state.applicable
    ]
    lines += [
        "## Exclusions",
        "",
    ]
    if not excluded:
        lines += ["No Annex A control has been excluded.", ""]
    else:
        lines += [
            f"{len(excluded)} control(s) are excluded from the ISMS. Each exclusion is a",
            "claim that the control cannot apply within the scope above.",
            "",
            "| Control | Justification |",
            "| --- | --- |",
        ]
        for control_id, state in excluded:
            title = catalogue.control(control_id).title if catalogue.has_control(control_id) else ""
            lines.append(
                f"| {control_id} {title} | {(state.justification or '').strip() or '*(none)*'} |"
            )
        lines.append("")

    lines += [
        "## Management-system requirements",
        "",
        "Annex A is not the whole standard. Clauses 4 to 10 are what the certificate is",
        "granted against.",
        "",
        "| Requirement | Clause | Status | Mandatory |",
        "| --- | --- | --- | --- |",
    ]
    for requirement in catalogue.requirements:
        requirement_state = snapshot.requirement_states.get(requirement.id)
        status = (
            "Not recorded"
            if requirement_state is None
            else requirement_state.status.replace("_", " ").capitalize()
        )
        lines.append(
            f"| {requirement.id} {requirement.title} | {requirement.clause} | {status} | "
            f"{'Yes' if requirement.mandatory_for_certification else 'No'} |"
        )

    lines += [
        "",
        "---",
        "",
        f"Prepared from assessment {assessment.assessment_id or '(unsaved)'} on "
        f"{assessment.run_at.date().isoformat()}. "
        f"Catalogue fingerprint `{assessment.catalogue_fingerprint}`.",
        "",
    ]
    return "\n".join(lines)


def render_soa_csv(assessment: Assessment, snapshot: RegisterSnapshot) -> str:
    """The same content as CSV, for people who will open it in a spreadsheet.

    They will. An SoA is negotiated in a spreadsheet at nearly every
    organisation, and a tool that refuses to produce one is a tool that gets
    replaced by the spreadsheet rather than feeding it.
    """
    import csv
    import io

    treated = _risks_treated_by(snapshot)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "control_id",
            "theme",
            "title",
            "applicable",
            "status",
            "owner_role",
            "maturity",
            "implemented_on",
            "evidence_count",
            "evidence_newest",
            "evidence_stale",
            "risks_treated",
            "justification",
        ]
    )
    for control in snapshot.catalogue.controls:
        state = snapshot.control_states.get(control.id)
        fresh = snapshot.freshness.get(control.id, {})
        writer.writerow(
            [
                control.id,
                control.theme,
                control.title,
                "" if state is None else ("yes" if state.applicable else "no"),
                "" if state is None else state.status,
                "" if state is None else (state.owner_role or ""),
                "" if state is None else state.maturity,
                "" if state is None or state.implemented_on is None else state.implemented_on,
                fresh.get("count", 0),
                fresh.get("newest") or "",
                # Blank rather than "yes" when there is no evidence: a control
                # with nothing on file is unevidenced, not stale.
                ("yes" if fresh.get("stale") else "no") if fresh.get("count") else "",
                " ".join(treated.get(control.id, [])),
                _justification(control.id, state, treated).replace("\n", " "),
            ]
        )
    return buffer.getvalue()


def soa_filename(assessment: Assessment, extension: str = "md") -> str:
    date = assessment.run_at.date().isoformat()
    return f"soa-{date}-assessment-{assessment.assessment_id or 'draft'}.{extension}"


def today_iso() -> str:
    return dt.date.today().isoformat()
