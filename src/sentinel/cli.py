"""Command-line interface.

One command per operation in :mod:`sentinel.pipeline`. Nothing here contains
logic; if a command needs a decision made, the decision belongs upstream and
the command exists only to print it.

Exit codes: 0 success, 1 handled failure, 2 misuse, 3 refused by the gate. The
last is separate because "the organisation is not ready" is not a crash, and a
pipeline that runs `sentinel readiness` nightly has to be able to tell the
difference without parsing text.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from sentinel import __version__, pipeline
from sentinel.assessment.engine import (
    assessment_history,
    build_snapshot,
    latest_assessment,
    run_assessment,
)
from sentinel.catalogue import get_catalogue
from sentinel.config import get_settings
from sentinel.db.engine import check_connection
from sentinel.db.schema import drop_schema, schema_exists, table_counts
from sentinel.exceptions import ReadinessBlocked, SentinelError
from sentinel.logging_config import configure_logging
from sentinel.registers.controls import latest_activity, load_control_states
from sentinel.registers.evidence import freshness, load_evidence
from sentinel.registers.risks import Acceptance, accept_risk, list_risks, risk_posture

app = typer.Typer(
    add_completion=False,
    help="sentinel -- ISO/IEC 27001:2022 compliance engine for a simulated organisation.",
)
console = Console()

EXIT_FAILURE, EXIT_MISUSE, EXIT_BLOCKED = 1, 2, 3

SEVERITY_COLOUR = {
    "blocking": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim",
}


def _fail(message: str, code: int = EXIT_FAILURE) -> NoReturn:
    console.print(f"[bold red]FAILED[/bold red] {message}")
    raise typer.Exit(code=code)


def _require_database() -> None:
    settings = get_settings()
    if not check_connection(settings, retries=5):
        _fail("database unreachable -- check .env and that PostgreSQL is running")
    if not schema_exists(settings):
        _fail("the ISMS schema does not exist yet -- run `sentinel init-db`")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    if version:
        catalogue = get_catalogue(settings)
        console.print(f"sentinel {__version__} (catalogue {catalogue.version})")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Check configuration, the database, the catalogue and the registers."""
    settings = get_settings()
    table = Table(title="sentinel doctor", header_style="bold")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("version", __version__)
    table.add_row("organisation", settings.organisation)
    table.add_row("database", settings.safe_dsn)
    table.add_row("schema", settings.schema_name)
    table.add_row(
        "risk appetite",
        f"{settings.risk_appetite} (escalation at {settings.risk_escalation})",
    )
    table.add_row(
        "gate",
        f"{settings.min_implemented_ratio:.0%} implemented, "
        f"{settings.max_controls_without_evidence} unevidenced tolerated, "
        f"clause 9 {'required' if settings.require_internal_audit else 'not required'}",
    )

    try:
        catalogue = get_catalogue(settings)
        table.add_row(
            "catalogue",
            f"{catalogue.version} -- {len(catalogue.controls)} controls, "
            f"{len(catalogue.requirements)} requirements",
        )
        table.add_row("fingerprint", catalogue.fingerprint[:32] + "...")
    except SentinelError as exc:
        table.add_row("catalogue", f"[red]{exc}[/red]")

    reachable = check_connection(settings, retries=2)
    table.add_row("database reachable", "[green]yes[/green]" if reachable else "[red]no[/red]")
    if reachable:
        # A reachable database is not an initialised one, and a diagnostic
        # command that crashes on the state it exists to diagnose is useless.
        if schema_exists(settings):
            counts = table_counts(settings)
            table.add_row(
                "registers",
                f"{counts.get('asset', 0)} assets, {counts.get('risk', 0)} risks, "
                f"{counts.get('control_state', 0)} control decisions, "
                f"{counts.get('evidence', 0)} evidence items",
            )
            table.add_row("assessments", str(counts.get("assessment", 0)))
        else:
            table.add_row("schema", "[yellow]not initialised -- run `sentinel init-db`[/yellow]")
    console.print(table)
    if not reachable:
        raise typer.Exit(code=EXIT_FAILURE)


@app.command(name="init-db")
def init_db() -> None:
    """Create the ISMS schema and its tables. Safe to re-run."""
    try:
        applied = pipeline.prepare()
    except SentinelError as exc:
        _fail(str(exc))
    console.print(
        f"[bold green]OK[/bold green] schema ready ({sum(applied.values())} statements applied)"
    )


@app.command()
def catalogue(
    theme: Annotated[str | None, typer.Option(help="Restrict to one theme.")] = None,
    capability: Annotated[str | None, typer.Option(help="Restrict to one capability.")] = None,
) -> None:
    """Show the control catalogue."""
    loaded = get_catalogue()
    controls = loaded.controls
    if theme:
        controls = loaded.by_theme(theme)
        if not controls:
            _fail(
                f"unknown theme {theme!r}; expected one of {', '.join(loaded.themes)}",
                EXIT_MISUSE,
            )
    if capability:
        controls = tuple(c for c in controls if c.capability == capability)
        if not controls:
            _fail(f"no controls with capability {capability!r}", EXIT_MISUSE)

    table = Table(
        title=f"Catalogue {loaded.version} ({len(controls)} controls)", header_style="bold"
    )
    for column in ("control", "title", "theme", "capability", "evidence valid"):
        table.add_column(column)
    for control in controls:
        table.add_row(
            control.id,
            control.title,
            control.theme,
            control.capability,
            f"{control.evidence_valid_days} d",
        )
    console.print(table)
    console.print(f"[dim]{loaded.framework_note.strip()}[/dim]")


@app.command()
def demo(
    profile: Annotated[
        str, typer.Option(help="'gaps' (an ISMS mid-programme) or 'ready' (after remediation).")
    ] = "gaps",
) -> None:
    """Populate the registers with the simulated organisation."""
    if profile not in ("gaps", "ready"):
        _fail(f"unknown profile {profile!r}; expected 'gaps' or 'ready'", EXIT_MISUSE)
    try:
        report = pipeline.seed_demo(profile)  # type: ignore[arg-type]
    except SentinelError as exc:
        _fail(str(exc))
    table = Table(title=f"Demo organisation ({profile})", header_style="bold")
    table.add_column("register")
    table.add_column("rows", justify="right")
    for key in ("assets", "risks", "controls_decided", "evidence_items", "requirements"):
        table.add_row(key.replace("_", " "), f"{report[key]:,}")
    console.print(table)
    if report["planted_defects"]:
        console.print(
            f"[yellow]{report['planted_defects']} defects planted on purpose[/yellow] -- "
            "see docs/demo.md for the list the engine is expected to find"
        )


# ---------------------------------------------------------------------------
# The registers
# ---------------------------------------------------------------------------


@app.command()
def risks(
    above_appetite: Annotated[bool, typer.Option(help="Only those above appetite.")] = False,
    limit: Annotated[int, typer.Option(help="How many to print.")] = 30,
) -> None:
    """Show the risk register."""
    _require_database()
    settings = get_settings()
    register = list_risks(settings)
    if above_appetite:
        register = [risk for risk in register if risk.above_appetite(settings)]
    register.sort(key=lambda risk: (-risk.effective_score, risk.risk_id))

    table = Table(title=f"Risk register ({len(register)})", header_style="bold")
    for column in ("risk", "title", "owner", "inherent", "residual", "band", "acceptance"):
        table.add_column(column, justify="right" if column in ("inherent", "residual") else "left")
    for risk in register[:limit]:
        acceptance = risk.acceptance
        if acceptance is None:
            signed = "[dim]none[/dim]"
        elif not acceptance.covers(risk.effective_score):
            signed = f"[red]stale ({acceptance.residual_at_acceptance})[/red]"
        else:
            signed = acceptance.accepted_role
        colour = {"critical": "bold red", "high": "red", "medium": "yellow"}.get(risk.band, "green")
        table.add_row(
            risk.risk_id,
            risk.title[:44],
            risk.owner_role,
            str(risk.inherent_score),
            "—" if risk.residual_score is None else str(risk.residual_score),
            f"[{colour}]{risk.band}[/{colour}]",
            signed,
        )
    console.print(table)
    posture = risk_posture(list_risks(settings), settings)
    console.print(
        f"[bold]{posture['above_appetite']}[/bold] above an appetite of "
        f"{posture['appetite']}, [bold]{posture['accepted']}[/bold] with an acceptance on file; "
        f"treatment has removed [bold]{posture['reduction_pct']}%[/bold] of the inherent total"
    )


@app.command()
def controls(
    status: Annotated[str | None, typer.Option(help="Filter by status.")] = None,
    theme: Annotated[str | None, typer.Option(help="Filter by theme.")] = None,
    outstanding: Annotated[bool, typer.Option(help="Only what is not implemented.")] = False,
) -> None:
    """Show the applicability decisions and their state."""
    _require_database()
    settings = get_settings()
    loaded = get_catalogue(settings)
    states = load_control_states(settings)

    table = Table(title="Control state", header_style="bold")
    for column in ("control", "title", "applicable", "status", "owner", "maturity"):
        table.add_column(column)
    shown = 0
    for control in loaded.controls:
        if theme and control.theme != theme:
            continue
        state = states.get(control.id)
        current = "not decided" if state is None else state.status
        if status and current != status:
            continue
        if outstanding and current == "implemented":
            continue
        colour = {
            "implemented": "green",
            "excluded": "dim",
            "not decided": "bold red",
            "not_started": "red",
        }.get(current, "yellow")
        table.add_row(
            control.id,
            control.title[:44],
            "—" if state is None else ("yes" if state.applicable else "no"),
            f"[{colour}]{current}[/{colour}]",
            "—" if state is None else (state.owner_role or "[red]none[/red]"),
            "—" if state is None else f"{state.maturity}/5",
        )
        shown += 1
    console.print(table)
    console.print(f"[dim]{shown} of {len(loaded.controls)} controls shown[/dim]")


@app.command()
def evidence(
    stale_only: Annotated[bool, typer.Option("--stale", help="Only stale or missing.")] = False,
) -> None:
    """Show evidence coverage and freshness per control."""
    _require_database()
    settings = get_settings()
    loaded = get_catalogue(settings)
    status = freshness(load_evidence(settings), loaded, settings)

    table = Table(title="Evidence", header_style="bold")
    for column in ("control", "items", "newest", "valid for", "state"):
        table.add_column(column)
    for control in loaded.controls:
        entry = status.get(control.id, {})
        count = int(entry.get("count", 0))
        if count == 0:
            state, colour = "none on file", "red"
        elif entry.get("stale"):
            state, colour = f"stale by {abs(int(entry['days_remaining']))} d", "red"
        elif entry.get("expiring_soon"):
            state, colour = f"expires in {entry['days_remaining']} d", "yellow"
        else:
            state, colour = f"{entry['days_remaining']} d remaining", "green"
        if stale_only and colour == "green":
            continue
        table.add_row(
            control.id,
            str(count),
            str(entry.get("newest") or "—"),
            f"{entry.get('valid_days', control.evidence_valid_days)} d",
            f"[{colour}]{state}[/{colour}]",
        )
    console.print(table)


@app.command(name="accept-risk")
def accept_risk_command(
    risk_id: Annotated[str, typer.Argument(help="Which risk.")],
    by: Annotated[str, typer.Option(help="Who signed.")],
    role: Annotated[str, typer.Option(help="In what role.")],
    rationale: Annotated[str, typer.Option(help="Why the risk is being accepted.")],
    expires_in_days: Annotated[int, typer.Option(help="How long the acceptance stands.")] = 365,
) -> None:
    """Record a signed acceptance of a residual risk."""
    _require_database()
    settings = get_settings()
    register = {risk.risk_id: risk for risk in list_risks(settings)}
    risk = register.get(risk_id)
    if risk is None:
        _fail(f"no risk {risk_id!r} in the register", EXIT_MISUSE)
    if not rationale.strip():
        _fail("an acceptance needs a rationale", EXIT_MISUSE)

    acceptance = Acceptance(
        risk_id=risk.risk_id,
        accepted_by=by,
        accepted_role=role,
        rationale=rationale,
        residual_at_acceptance=risk.effective_score,
        accepted_on=dt.date.today(),
        expires_on=dt.date.today() + dt.timedelta(days=expires_in_days),
    )
    try:
        accept_risk(acceptance, settings)
    except SentinelError as exc:
        _fail(str(exc))
    console.print(
        f"[bold green]OK[/bold green] {risk.risk_id} accepted at {risk.effective_score} by {role}"
    )
    if risk.needs_management_signature(settings) and "management" not in role.lower():
        console.print(
            f"[yellow]Note[/yellow] this risk is at or above the escalation level "
            f"({settings.risk_escalation}); the next assessment will report the signature "
            "as being below top management."
        )


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def _findings_table(findings: list[Any], title: str) -> Table:
    table = Table(title=title, header_style="bold")
    for column in ("severity", "rule", "subject", "detail"):
        table.add_column(column)
    for finding in findings:
        colour = SEVERITY_COLOUR[finding.severity]
        table.add_row(
            f"[{colour}]{finding.severity}[/{colour}]",
            finding.rule,
            finding.subject_id,
            finding.detail[:88],
        )
    return table


@app.command()
def assess(
    show: Annotated[int, typer.Option(help="How many findings to print.")] = 20,
    no_save: Annotated[bool, typer.Option("--no-save", help="Do not keep this run.")] = False,
) -> None:
    """Run every rule and the gate, and record the result."""
    _require_database()
    try:
        assessment = run_assessment(persist=not no_save)
    except SentinelError as exc:
        _fail(str(exc))

    totals = assessment.totals
    summary = Table(
        title=f"Assessment {assessment.assessment_id or '(not saved)'}", header_style="bold"
    )
    summary.add_column("measure")
    summary.add_column("value", justify="right")
    summary.add_row("controls assessed", f"{totals['assessed']} of {totals['controls']}")
    summary.add_row("applicable", str(totals["applicable"]))
    summary.add_row("implemented", f"{totals['implemented']} ({totals['implementation_pct']}%)")
    summary.add_row("currently evidenced", f"{totals['evidenced']} ({totals['evidence_pct']}%)")
    summary.add_row("mean maturity", f"{totals['mean_maturity']} / 5")
    summary.add_row(
        "clause requirements met", f"{totals['requirements_met']} of {totals['requirements']}"
    )
    summary.add_row(
        "risks above appetite",
        f"{totals['risk']['above_appetite']} of {totals['risk']['risks']}",
    )
    console.print(summary)

    from sentinel.assessment.rules import summarise

    counts = summarise(assessment.findings)
    console.print(
        " · ".join(
            f"[{SEVERITY_COLOUR[severity]}]{count} {severity}[/{SEVERITY_COLOUR[severity]}]"
            for severity, count in counts.items()
        )
    )
    if assessment.findings:
        console.print(_findings_table(assessment.findings[:show], "Findings (worst first)"))
        if len(assessment.findings) > show:
            console.print(
                f"[dim]{len(assessment.findings) - show} more -- `sentinel findings` for all[/dim]"
            )

    verdict = (
        "[bold green]the gate would pass[/bold green]"
        if assessment.decision.ready
        else f"[bold red]the gate refuses[/bold red]: {len(assessment.decision.failed)} check(s) failed"
    )
    console.print(Panel(verdict, title="readiness", border_style="cyan"))


@app.command()
def findings(
    severity: Annotated[str | None, typer.Option(help="Filter by severity.")] = None,
    rule: Annotated[str | None, typer.Option(help="Filter by rule.")] = None,
) -> None:
    """Show the findings from the most recent assessment."""
    _require_database()
    stored = latest_assessment()
    if stored is None:
        _fail("no assessment has been run yet -- `sentinel assess`")
    rows = stored["findings"]
    if severity:
        rows = [row for row in rows if row["severity"] == severity]
    if rule:
        rows = [row for row in rows if row["rule"] == rule]

    table = Table(
        title=f"Findings from assessment {stored['assessment_id']} ({len(rows)})",
        header_style="bold",
    )
    for column in ("severity", "rule", "subject", "detail", "remediation"):
        table.add_column(column)
    for row in rows:
        colour = SEVERITY_COLOUR[row["severity"]]
        table.add_row(
            f"[{colour}]{row['severity']}[/{colour}]",
            row["rule"],
            row["subject_id"],
            row["detail"][:60],
            row["remediation"][:60],
        )
    console.print(table)


@app.command()
def readiness(
    force: Annotated[bool, typer.Option(help="Declare ready even if the gate refuses.")] = False,
    reason: Annotated[str | None, typer.Option(help="Why, when forcing.")] = None,
) -> None:
    """Ask whether the ISMS would survive a certification audit today.

    Exit code 3 when the gate refuses -- not 1. A refusal is the tool working.
    """
    _require_database()
    try:
        assessment = run_assessment()
    except SentinelError as exc:
        _fail(str(exc))

    table = Table(title="Audit readiness gate", header_style="bold")
    for column in ("check", "result", "detail"):
        table.add_column(column)
    for check in assessment.decision.checks:
        mark = (
            "[dim]skip[/dim]"
            if check.skipped
            else ("[green]pass[/green]" if check.passed else "[bold red]FAIL[/bold red]")
        )
        table.add_row(check.name, mark, check.detail[:92])
    console.print(table)

    try:
        pipeline.declare_ready(assessment, force=force, reason=reason)
    except ReadinessBlocked as exc:
        console.print(f"[bold red]NOT READY[/bold red] {exc}")
        console.print(
            "[dim]Fix the blocking findings, or pass --force with a --reason; the override "
            "is recorded on the assessment.[/dim]"
        )
        raise typer.Exit(code=EXIT_BLOCKED) from exc

    if assessment.forced:
        console.print(
            f"[yellow]DECLARED READY over a refused gate[/yellow] -- reason recorded: {reason}"
        )
    else:
        console.print("[bold green]READY[/bold green] every gate check passed")


@app.command()
def export(
    kind: Annotated[str, typer.Option(help="all | soa | plan | findings | dossier")] = "all",
) -> None:
    """Write the documents for the most recent assessment."""
    _require_database()
    try:
        assessment = run_assessment()
        written = pipeline.export_all(assessment)
    except SentinelError as exc:
        _fail(str(exc))

    wanted = {
        "soa": ("soa-",),
        "plan": ("risk-treatment-plan-",),
        "findings": ("findings-",),
        "dossier": ("audit-dossier-",),
        "all": ("",),
    }.get(kind)
    if wanted is None:
        _fail(f"unknown kind {kind!r}", EXIT_MISUSE)

    table = Table(title="Exports", header_style="bold")
    table.add_column("document")
    table.add_column("path")
    for name, path in sorted(written.items()):
        if any(name.startswith(prefix) for prefix in wanted):
            table.add_row(name, str(path))
    console.print(table)


@app.command()
def posture() -> None:
    """The one-screen view: coverage by capability, and what is weakest."""
    _require_database()
    settings = get_settings()
    assessment = run_assessment(persist=False)
    by_capability = assessment.totals["by_capability"]

    table = Table(title=f"Posture -- {settings.organisation}", header_style="bold")
    for column in ("capability", "applicable", "implemented", "coverage"):
        table.add_column(column, justify="right" if column != "capability" else "left")
    for capability, bucket in sorted(
        by_capability.items(),
        key=lambda item: (
            item[1]["implemented"] / item[1]["applicable"] if item[1]["applicable"] else 1.0
        ),
    ):
        applicable = bucket["applicable"]
        implemented = bucket["implemented"]
        pct = 100.0 * implemented / applicable if applicable else 100.0
        colour = "green" if pct >= 95 else ("yellow" if pct >= 75 else "red")
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        table.add_row(
            capability,
            str(applicable),
            str(implemented),
            f"[{colour}]{bar} {pct:5.1f}%[/{colour}]",
        )
    console.print(table)

    snapshot = build_snapshot(settings)
    for label, kind in (
        ("internal audit", "internal_audit"),
        ("management review", "management_review"),
    ):
        activity = latest_activity(kind, settings)
        if activity is None:
            console.print(f"[red]no {label} has ever been recorded[/red]")
        else:
            age = (snapshot.today - activity["performed_on"]).days
            colour = "green" if age <= 365 else "red"
            console.print(
                f"[{colour}]last {label}: {activity['performed_on']} ({age} days ago)[/{colour}]"
            )


@app.command()
def history(limit: Annotated[int, typer.Option(help="How many.")] = 10) -> None:
    """Show past assessments: the trend is the point."""
    _require_database()
    rows = assessment_history(limit)
    if not rows:
        console.print("[yellow]no assessments yet[/yellow]")
        return
    table = Table(title="Assessments", header_style="bold")
    for column in ("id", "run at", "catalogue", "implemented", "blocking", "verdict"):
        table.add_column(column)
    for row in rows:
        totals = row["totals"] or {}
        verdict = (
            "[green]ready[/green]"
            if row["ready"] and not row["forced"]
            else ("[yellow]forced[/yellow]" if row["forced"] else "[red]not ready[/red]")
        )
        table.add_row(
            str(row["assessment_id"]),
            row["run_at"].strftime("%Y-%m-%d %H:%M"),
            row["catalogue_version"],
            f"{totals.get('implementation_pct', 0)}%",
            str(row["blocking_findings"]),
            verdict,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


@app.command(name="record-activity")
def record_activity_command(
    kind: Annotated[str, typer.Argument(help="internal_audit | management_review | risk_review")],
    by: Annotated[str, typer.Option(help="Who performed it.")],
    summary: Annotated[str, typer.Option(help="What it found.")],
    on: Annotated[str | None, typer.Option(help="YYYY-MM-DD; defaults to today.")] = None,
) -> None:
    """Record an internal audit, a management review or a risk review."""
    _require_database()
    from sentinel.registers.controls import record_activity

    performed_on = dt.date.today()
    if on:
        try:
            performed_on = dt.date.fromisoformat(on)
        except ValueError:
            _fail(f"invalid date {on!r}", EXIT_MISUSE)
    try:
        activity_id = record_activity(kind, performed_on, by, summary)
    except SentinelError as exc:
        _fail(str(exc))
    console.print(f"[bold green]OK[/bold green] {kind} recorded ({activity_id}) on {performed_on}")


@app.command()
def cycle(
    profile: Annotated[str, typer.Option(help="'gaps' or 'ready'.")] = "gaps",
) -> None:
    """Seed, assess, export and gate in one command -- what CI runs."""
    if profile not in ("gaps", "ready"):
        _fail(f"unknown profile {profile!r}", EXIT_MISUSE)
    try:
        result = pipeline.full_cycle(profile)  # type: ignore[arg-type]
    except SentinelError as exc:
        _fail(str(exc))

    assessment = result["assessment"]
    table = Table(title=f"Cycle ({profile})", header_style="bold")
    table.add_column("field")
    table.add_column("value", justify="right")
    table.add_row("assessment", str(assessment["assessment_id"]))
    table.add_row("implemented", f"{assessment['totals']['implementation_pct']}%")
    for severity, count in assessment["findings"].items():
        table.add_row(f"{severity} findings", str(count))
    table.add_row("documents", str(len(result["exports"])))
    table.add_row("ready", "yes" if assessment["ready"] else "no")
    console.print(table)
    if result["blocked"]:
        console.print(f"[bold red]NOT READY[/bold red] {result['blocked']}")
        raise typer.Exit(code=EXIT_BLOCKED)
    console.print("[bold green]READY[/bold green]")


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload on code changes.")] = False,
) -> None:
    """Run the read-only posture API."""
    import uvicorn

    settings = get_settings()
    console.print(f"[bold]sentinel[/bold] on http://{host}:{port}")
    uvicorn.run(
        "sentinel.api.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command()
def reset(
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask.")] = False,
) -> None:
    """Drop the ISMS schema.

    Every assessment, decision, acceptance and evidence record goes with it.
    """
    settings = get_settings()
    if not yes and not typer.confirm(
        f"Drop schema {settings.schema_name} on {settings.safe_dsn}? "
        "This destroys every assessment and evidence record.",
        default=False,
    ):
        console.print("Cancelled.")
        raise typer.Exit()
    try:
        drop_schema(settings)
    except SentinelError as exc:
        _fail(str(exc))
    console.print("[bold green]OK[/bold green] schema dropped")


if __name__ == "__main__":  # pragma: no cover
    app()
