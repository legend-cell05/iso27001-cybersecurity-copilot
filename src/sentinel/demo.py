"""A simulated organisation, for demonstrating and testing the engine.

Meridian Logistics does not exist. Its assets, risks, control decisions and
evidence are generated here, deterministically from a seed, and every number
this project reports comes from them.

Two profiles, because the interesting thing about a compliance engine is not
that it can say yes:

``gaps``  an ISMS nine months into a programme, with the failures that
          actually happen -- a control nobody decided about, an acceptance
          signed against a score the risk no longer has, residual scores
          resting on controls that are still "planned", evidence that has
          quietly aged out, and no management review. The gate refuses.

``ready`` the same organisation after the remediation, where the gate passes.
          It exists so that "the gate can be satisfied" is demonstrated rather
          than asserted -- a gate that has never passed is as untrustworthy as
          one that has never refused.

The defects in ``gaps`` are deliberate and enumerated in ``docs/demo.md``, so
that a reader can check the engine found what was planted and nothing else.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass
from typing import Any, Literal

from sentinel.catalogue import Catalogue, get_catalogue
from sentinel.config import Settings, get_settings
from sentinel.logging_config import get_logger
from sentinel.registers.assets import Asset, upsert_assets
from sentinel.registers.controls import (
    ControlState,
    RequirementState,
    record_activity,
    upsert_control_states,
    upsert_requirement_states,
)
from sentinel.registers.evidence import Evidence, record_evidence
from sentinel.registers.risks import Acceptance, Risk, accept_risk, upsert_risks

logger = get_logger(__name__)

Profile = Literal["gaps", "ready"]

# The reference date the demo is built around. Fixed rather than "today" so
# that the ages of evidence, acceptances and audits are reproducible: a demo
# whose findings change with the calendar cannot be documented.
TODAY = dt.date(2026, 9, 27)

# Why a control is in the SoA when no risk in the register names it. Real
# Statements of Applicability say this; one whose justification column reads
# "best practice" ninety-three times is a document nobody believes.
BASELINE_JUSTIFICATIONS = {
    "compliance": "Required to meet the contractual and regulatory obligations in the register.",
    "governance": "Part of the ISMS baseline approved by top management.",
    "human_resources": "Required by the organisation's employment and onboarding process.",
    "information_protection": "Required by the classification scheme applied across the scope.",
    "physical_security": "Applies to both sites within the scope of the ISMS.",
    "operations": "Required to run the in-scope systems in a controlled way.",
    "secure_development": "Applies to the booking platform, which is developed in house.",
    "supplier_management": "Applies to the carriers, the broker portal and the cloud services in scope.",
    "asset_management": "Required to keep the asset inventory the risk method depends on.",
    "identity_and_access": "Required by the access control policy across the in-scope systems.",
    "network_security": "Applies to the corporate and depot networks within the scope.",
    "endpoint_security": "Applies to the laptops and handhelds used to reach in-scope systems.",
    "threat_and_vulnerability": "Required to keep the risk assessment current between reviews.",
    "detection_and_response": "Required so that incidents are noticed within the agreed response times.",
    "incident_management": "Required by the incident process the ISMS commits to.",
    "continuity": "Required by the availability objectives agreed with customers.",
}

ROLES = (
    "Head of IT",
    "Information Security Officer",
    "Operations Director",
    "HR Manager",
    "Finance Manager",
    "Site Manager, Lyon",
    "Lead Developer",
    "Data Protection Officer",
)


@dataclass(frozen=True)
class DemoReport:
    profile: str
    assets: int
    risks: int
    controls_decided: int
    evidence_items: int
    requirements: int
    planted_defects: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "assets": self.assets,
            "risks": self.risks,
            "controls_decided": self.controls_decided,
            "evidence_items": self.evidence_items,
            "requirements": self.requirements,
            "planted_defects": len(self.planted_defects),
        }


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

ASSETS: tuple[tuple[str, str, str, str, str, int, str | None], ...] = (
    ("AST-001", "Freight booking platform", "software", "Head of IT", "confidential", 5, "Lyon"),
    (
        "AST-002",
        "Customer database",
        "information",
        "Data Protection Officer",
        "restricted",
        5,
        "Lyon",
    ),
    ("AST-003", "Consignment tracking API", "service", "Head of IT", "confidential", 4, "Lyon"),
    (
        "AST-004",
        "Carrier contracts repository",
        "information",
        "Operations Director",
        "confidential",
        4,
        "Lille",
    ),
    ("AST-005", "Payroll records", "information", "HR Manager", "restricted", 4, "Lille"),
    (
        "AST-006",
        "Invoicing and ledger system",
        "software",
        "Finance Manager",
        "confidential",
        4,
        "Lille",
    ),
    (
        "AST-007",
        "Warehouse handheld terminals",
        "hardware",
        "Site Manager, Lyon",
        "internal",
        3,
        "Lyon",
    ),
    ("AST-008", "Corporate laptops", "hardware", "Head of IT", "internal", 3, None),
    ("AST-009", "Lyon data room", "site", "Site Manager, Lyon", "confidential", 5, "Lyon"),
    ("AST-010", "Lille depot network", "site", "Operations Director", "internal", 3, "Lille"),
    ("AST-011", "Source code repository", "software", "Lead Developer", "confidential", 4, None),
    (
        "AST-012",
        "CI/CD pipeline and build secrets",
        "software",
        "Lead Developer",
        "restricted",
        5,
        None,
    ),
    ("AST-013", "Identity provider", "service", "Head of IT", "restricted", 5, None),
    ("AST-014", "Backup vault", "service", "Head of IT", "restricted", 5, None),
    ("AST-015", "Email and collaboration suite", "service", "Head of IT", "confidential", 4, None),
    (
        "AST-016",
        "Customer support ticketing",
        "service",
        "Operations Director",
        "confidential",
        3,
        None,
    ),
    (
        "AST-017",
        "Telematics feed from carriers",
        "information",
        "Operations Director",
        "internal",
        3,
        None,
    ),
    ("AST-018", "Operations and warehouse staff", "people", "HR Manager", "internal", 4, None),
    (
        "AST-019",
        "Third-party customs broker portal",
        "service",
        "Operations Director",
        "confidential",
        3,
        None,
    ),
    ("AST-020", "Network firewalls and VPN", "hardware", "Head of IT", "confidential", 5, None),
    (
        "AST-021",
        "Log and monitoring platform",
        "service",
        "Information Security Officer",
        "confidential",
        4,
        None,
    ),
    (
        "AST-022",
        "Paper consignment notes archive",
        "information",
        "Site Manager, Lyon",
        "internal",
        2,
        "Lyon",
    ),
)


# ---------------------------------------------------------------------------
# Risks
#
# threat / vulnerability pairs drawn from what actually happens to a logistics
# company: credential theft, a leaving driver with a handheld, a carrier
# integration nobody monitors, ransomware through an unpatched depot host.
# ---------------------------------------------------------------------------

RISKS: tuple[tuple[str, str, str, str, str, str, int, int, tuple[str, ...]], ...] = (
    (
        "RSK-001",
        "Customer data exposed through stolen credentials",
        "AST-002",
        "Data Protection Officer",
        "Credential theft by phishing",
        "Single-factor access to the booking platform",
        4,
        5,
        ("A.5.15", "A.5.17", "A.8.2", "A.8.5", "A.6.3"),
    ),
    (
        "RSK-002",
        "Ransomware encrypts the booking platform",
        "AST-001",
        "Head of IT",
        "Ransomware delivered by email or an exposed service",
        "Unpatched hosts and flat network segments",
        4,
        5,
        ("A.8.7", "A.8.8", "A.8.13", "A.8.22", "A.5.26"),
    ),
    (
        "RSK-003",
        "Backups cannot be restored when needed",
        "AST-014",
        "Head of IT",
        "Silent backup failure",
        "Restores are not tested",
        3,
        5,
        ("A.8.13", "A.5.30", "A.8.14"),
    ),
    (
        "RSK-004",
        "Former employee retains access after leaving",
        "AST-013",
        "HR Manager",
        "Leaver with live credentials",
        "Offboarding not linked to identity management",
        4,
        4,
        ("A.5.16", "A.5.18", "A.6.5", "A.5.11"),
    ),
    (
        "RSK-005",
        "Carrier integration leaks consignment data",
        "AST-017",
        "Operations Director",
        "Misconfigured third-party integration",
        "No review of what the integration is permitted to read",
        3,
        4,
        ("A.5.19", "A.5.21", "A.5.22", "A.8.21"),
    ),
    (
        "RSK-006",
        "Warehouse handheld lost with cached data",
        "AST-007",
        "Site Manager, Lyon",
        "Loss or theft of a mobile device",
        "No device encryption or remote wipe",
        4,
        3,
        ("A.7.9", "A.8.1", "A.7.10"),
    ),
    (
        "RSK-007",
        "Build secrets leaked from the CI pipeline",
        "AST-012",
        "Lead Developer",
        "Secret committed or exposed in build logs",
        "Secrets held in pipeline configuration",
        3,
        5,
        ("A.8.4", "A.5.17", "A.8.31", "A.8.28"),
    ),
    (
        "RSK-008",
        "Payroll data disclosed internally",
        "AST-005",
        "HR Manager",
        "Over-broad file share permissions",
        "Access rights never reviewed",
        3,
        4,
        ("A.5.18", "A.8.3", "A.5.12"),
    ),
    (
        "RSK-009",
        "Depot network used to reach the corporate network",
        "AST-010",
        "Head of IT",
        "Compromise of a depot device",
        "Depot and corporate networks are not segregated",
        3,
        4,
        ("A.8.22", "A.8.20", "A.8.16"),
    ),
    (
        "RSK-010",
        "Customs broker portal credentials shared between staff",
        "AST-019",
        "Operations Director",
        "Shared accounts",
        "No individual accounts on the broker portal",
        4,
        3,
        ("A.5.16", "A.5.17", "A.8.5"),
    ),
    (
        "RSK-011",
        "Invoice fraud through supplier bank-detail change",
        "AST-006",
        "Finance Manager",
        "Business email compromise",
        "Bank detail changes accepted by email alone",
        3,
        5,
        ("A.6.3", "A.5.14", "A.8.23", "A.5.24"),
    ),
    (
        "RSK-012",
        "Source code disclosed to a third party",
        "AST-011",
        "Lead Developer",
        "Unauthorised repository access",
        "Repository permissions granted broadly and never reviewed",
        2,
        4,
        ("A.8.4", "A.5.18", "A.8.31"),
    ),
    (
        "RSK-013",
        "Lyon data room accessed by an unauthorised visitor",
        "AST-009",
        "Site Manager, Lyon",
        "Tailgating into a secure area",
        "Entry controls not enforced during deliveries",
        3,
        4,
        ("A.7.1", "A.7.2", "A.7.4", "A.7.6"),
    ),
    (
        "RSK-014",
        "Power failure takes out the Lyon comms room",
        "AST-009",
        "Site Manager, Lyon",
        "Utility failure",
        "No tested UPS or generator arrangement",
        2,
        4,
        ("A.7.11", "A.8.14", "A.5.30"),
    ),
    (
        "RSK-015",
        "Personal data kept beyond its retention period",
        "AST-002",
        "Data Protection Officer",
        "Retention obligations not applied",
        "No deletion process for closed accounts",
        4,
        3,
        ("A.5.34", "A.8.10", "A.5.33"),
    ),
    (
        "RSK-016",
        "Security incident goes unnoticed for weeks",
        "AST-021",
        "Information Security Officer",
        "Undetected intrusion",
        "Logs are collected but not reviewed",
        4,
        4,
        ("A.8.15", "A.8.16", "A.5.25", "A.8.17"),
    ),
    (
        "RSK-017",
        "Supplier outage stops customer bookings",
        "AST-003",
        "Operations Director",
        "Cloud provider outage",
        "No continuity arrangement for the booking path",
        2,
        4,
        ("A.5.29", "A.5.30", "A.8.14"),
    ),
    (
        "RSK-018",
        "Malware on a corporate laptop spreads to shared drives",
        "AST-008",
        "Head of IT",
        "Malware on an endpoint",
        "Endpoint protection not enforced on all devices",
        3,
        3,
        ("A.8.7", "A.8.1", "A.8.19"),
    ),
    (
        "RSK-019",
        "Sensitive consignment notes left on a desk overnight",
        "AST-022",
        "Site Manager, Lyon",
        "Casual disclosure on site",
        "No clear desk practice in the Lyon office",
        3,
        2,
        ("A.7.7", "A.5.10", "A.6.3"),
    ),
    (
        "RSK-020",
        "Change to the booking platform breaks an access control",
        "AST-001",
        "Lead Developer",
        "Unreviewed production change",
        "Changes deployed without security review",
        3,
        4,
        ("A.8.32", "A.8.29", "A.8.31", "A.5.37"),
    ),
    (
        "RSK-021",
        "Staff unaware of how to report a suspected phishing email",
        "AST-018",
        "HR Manager",
        "Delayed incident reporting",
        "No awareness programme covering reporting",
        3,
        3,
        ("A.6.3", "A.6.8", "A.5.24"),
    ),
    (
        "RSK-022",
        "Cryptographic keys for the tracking API mismanaged",
        "AST-003",
        "Head of IT",
        "Key compromise or loss",
        "No defined key management practice",
        2,
        5,
        ("A.8.24", "A.5.17", "A.8.5"),
    ),
    (
        "RSK-023",
        "Vulnerability in an internet-facing service exploited",
        "AST-003",
        "Information Security Officer",
        "Exploitation of a known vulnerability",
        "No systematic vulnerability management",
        4,
        4,
        ("A.8.8", "A.8.9", "A.8.29"),
    ),
    (
        "RSK-024",
        "Contract obligations on data location not met",
        "AST-004",
        "Operations Director",
        "Non-compliance with a customer contract",
        "No register of contractual security obligations",
        2,
        4,
        ("A.5.31", "A.5.20", "A.5.23"),
    ),
    (
        "RSK-025",
        "Test environment populated with live customer data",
        "AST-002",
        "Lead Developer",
        "Production data in a weaker environment",
        "No rule covering test data",
        3,
        4,
        ("A.8.33", "A.8.31", "A.8.11"),
    ),
    (
        "RSK-026",
        "Disgruntled insider exports the customer list",
        "AST-002",
        "Information Security Officer",
        "Malicious insider",
        "No monitoring of bulk exports",
        2,
        5,
        ("A.8.12", "A.8.16", "A.6.4", "A.5.18"),
    ),
    (
        "RSK-027",
        "Firewall rule base drifts from its documented state",
        "AST-020",
        "Head of IT",
        "Configuration drift",
        "No periodic review of rules",
        3,
        3,
        ("A.8.9", "A.8.20", "A.5.36"),
    ),
    (
        "RSK-028",
        "Email spoofing of Meridian to customers",
        "AST-015",
        "Head of IT",
        "Domain spoofing",
        "Email authentication records incomplete",
        3,
        3,
        ("A.8.20", "A.5.14", "A.6.3"),
    ),
)


def _hash_for(label: str) -> str:
    """A stable fake digest for a demo artefact.

    The evidence in the demo does not point at real files, and inventing a
    plausible-looking hash that is not the hash of anything would be the exact
    dishonesty this project argues against elsewhere. So it is derived from
    the artefact's own label: reproducible, verifiable, and obviously
    synthetic to anyone who checks.
    """
    return hashlib.sha256(f"meridian-demo::{label}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# The planted defects.
#
# Named here rather than scattered through the generator, so that the demo's
# own documentation and its tests can assert that the engine found exactly
# these and did not invent anything else.
# ---------------------------------------------------------------------------

UNASSESSED_CONTROL = "A.5.7"  # nobody decided whether it applies
PLANNED_CONTROLS = ("A.8.12", "A.8.24", "A.5.36")  # applicable, not yet done
NOT_STARTED_CONTROLS = ("A.8.11", "A.5.13")  # not begun at all
NO_EVIDENCE_CONTROLS = ("A.5.30", "A.8.17")  # claimed, unevidenced
STALE_EVIDENCE_CONTROLS = ("A.8.13", "A.8.7")  # evidence aged out
WRONG_EVIDENCE_KIND_CONTROL = "A.5.15"  # screenshot, not a policy
WEAKLY_EXCLUDED_CONTROL = "A.5.6"  # "not needed."
EXCLUDED_BUT_TREATING = "A.8.22"  # excluded, yet named by risks
UNOWNED_CONTROLS = ("A.7.12", "A.8.6")  # implemented, no owner
AD_HOC_CONTROLS = ("A.5.37", "A.7.7")  # implemented at maturity 1
RISK_WITH_UNKNOWN_CONTROL = "RSK-011"  # names A.5.99
RISK_WITHOUT_TREATMENT = "RSK-027"  # 'modify' with no controls
RISK_RESCORED_AFTER_ACCEPTANCE = "RSK-014"  # signature for another number
RISK_WITH_EXPIRED_ACCEPTANCE = "RSK-003"
RISK_ACCEPTED_TOO_LOW = "RSK-023"  # high risk, manager's signature
UNMET_MANDATORY_REQUIREMENTS = ("C.9.3", "C.6.2", "C.8.3")
UNMET_OPTIONAL_REQUIREMENT = "C.7.4"

PLANTED = [
    f"{UNASSESSED_CONTROL} has no applicability decision",
    f"{', '.join(PLANNED_CONTROLS)} are applicable and only planned",
    f"{', '.join(NOT_STARTED_CONTROLS)} are applicable and not started",
    f"{', '.join(NO_EVIDENCE_CONTROLS)} are claimed as implemented with no evidence",
    f"{', '.join(STALE_EVIDENCE_CONTROLS)} rest on evidence that has aged out",
    f"{WRONG_EVIDENCE_KIND_CONTROL} is evidenced by a screenshot where a policy is expected",
    f"{WEAKLY_EXCLUDED_CONTROL} is excluded with a one-line justification",
    f"{EXCLUDED_BUT_TREATING} is excluded yet named as treatment by two risks",
    f"{', '.join(UNOWNED_CONTROLS)} are implemented with no owning role",
    f"{', '.join(AD_HOC_CONTROLS)} are implemented at ad hoc maturity",
    f"{RISK_WITH_UNKNOWN_CONTROL} names a control identifier the catalogue does not contain",
    f"{RISK_WITHOUT_TREATMENT} is treated by modification and names no control",
    f"{RISK_RESCORED_AFTER_ACCEPTANCE} was re-scored upward after being accepted",
    f"{RISK_WITH_EXPIRED_ACCEPTANCE} carries an expired acceptance",
    f"{RISK_ACCEPTED_TOO_LOW} was accepted below the escalation level",
    "RSK-016 sits above appetite with no acceptance at all",
    "residual scores on RSK-026 rest on a control that is only planned",
    f"the mandatory requirements {', '.join(UNMET_MANDATORY_REQUIREMENTS)} are unmet",
    f"the requirement {UNMET_OPTIONAL_REQUIREMENT} is unmet",
    "no management review has ever been recorded",
    "the last internal audit was over a year ago",
]


def _assets() -> list[Asset]:
    return [
        Asset(
            asset_id=asset_id,
            name=name,
            kind=kind,
            owner_role=owner,
            classification=classification,
            criticality=criticality,
            site=site,
            description=None,
        )
        for asset_id, name, kind, owner, classification, criticality, site in ASSETS
    ]


def _risks(profile: Profile, rng: random.Random) -> list[Risk]:
    """Build the register, with the residual scores each profile earns."""
    risks: list[Risk] = []
    for (
        risk_id,
        title,
        asset_id,
        owner,
        threat,
        vulnerability,
        likelihood,
        impact,
        controls,
    ) in RISKS:
        control_ids = list(controls)
        treatment = "modify"
        status = "treated"
        review_due = TODAY + dt.timedelta(days=rng.randint(30, 300))

        if profile == "gaps":
            if risk_id == RISK_WITH_UNKNOWN_CONTROL:
                control_ids.append("A.5.99")
            if risk_id == RISK_WITHOUT_TREATMENT:
                control_ids = []
            if risk_id in ("RSK-002", "RSK-008"):
                review_due = TODAY - dt.timedelta(days=rng.randint(20, 120))

        # Residual: a real treatment takes roughly one point off each factor,
        # and never below 1. Untreated risks keep their inherent score.
        residual_likelihood: int | None = max(1, likelihood - 1)
        residual_impact: int | None = max(1, impact - 1)

        if profile == "gaps":
            # The risks whose treatment has not actually landed yet.
            if risk_id in ("RSK-016", "RSK-023", RISK_WITHOUT_TREATMENT):
                residual_likelihood = residual_impact = None
                status = "open"
            elif risk_id == "RSK-026":
                # Claims the reduction that A.8.12 would give, and A.8.12 is
                # only planned. This is the finding that catches a treatment
                # plan being reported as a treatment.
                residual_likelihood, residual_impact = 1, 3
            elif risk_id == RISK_RESCORED_AFTER_ACCEPTANCE:
                # Re-scored upward after somebody signed for a smaller number.
                residual_likelihood, residual_impact = 2, 4
        else:
            if risk_id == "RSK-026":
                residual_likelihood, residual_impact = 1, 5
            # In the remediated profile every treatment has landed, so the
            # reduction is real; the two heaviest risks get a second point.
            if risk_id in ("RSK-001", "RSK-002", "RSK-016", "RSK-023"):
                residual_likelihood = max(1, likelihood - 2)
                residual_impact = max(1, impact - 1)

        risks.append(
            Risk(
                risk_id=risk_id,
                title=title,
                description=None,
                asset_id=asset_id,
                threat=threat,
                vulnerability=vulnerability,
                owner_role=owner,
                likelihood=likelihood,
                impact=impact,
                treatment=treatment,
                residual_likelihood=residual_likelihood,
                residual_impact=residual_impact,
                status=status,
                identified_on=TODAY - dt.timedelta(days=rng.randint(120, 400)),
                review_due=review_due,
                controls=tuple(control_ids),
            )
        )
    return risks


def _control_states(
    profile: Profile, catalogue: Catalogue, rng: random.Random
) -> list[ControlState]:
    """One decision per Annex A control, with the gaps the profile calls for."""
    states: list[ControlState] = []
    for control in catalogue.controls:
        if profile == "gaps" and control.id == UNASSESSED_CONTROL:
            continue  # the hole in the SoA

        owner = rng.choice(ROLES)
        implemented_on: dt.date | None = TODAY - dt.timedelta(days=rng.randint(30, 500))
        maturity = rng.choice([3, 3, 3, 4, 4, 2])
        applicable = True
        justification: str | None = None
        status = "implemented"

        # Two exclusions that are genuinely defensible for this organisation,
        # in both profiles: no outsourced development, and no formal
        # application development beyond the booking platform's own team.
        if control.id == "A.8.30":
            applicable = False
            status = "excluded"
            justification = (
                "Meridian writes and maintains its own software; no development work is "
                "outsourced, so there is no outsourced development to direct or monitor. "
                "Reviewed at the last management review."
            )
        elif control.id == "A.7.14" and profile == "gaps":
            applicable = True
            status = "in_progress"
        elif profile == "gaps" and control.id == WEAKLY_EXCLUDED_CONTROL:
            applicable = False
            status = "excluded"
            justification = "Not needed."
        elif profile == "gaps" and control.id == EXCLUDED_BUT_TREATING:
            applicable = False
            status = "excluded"
            justification = (
                "The depot and corporate networks sit behind the same firewall and are "
                "treated as one trusted zone for now."
            )
        elif profile == "gaps" and control.id in PLANNED_CONTROLS:
            status = "planned"
        elif profile == "gaps" and control.id in NOT_STARTED_CONTROLS:
            status = "not_started"
        elif profile == "gaps" and control.id in AD_HOC_CONTROLS:
            maturity = 1

        if profile == "gaps" and control.id in UNOWNED_CONTROLS:
            owner = ""

        if status != "implemented":
            implemented_on = None
            if status != "excluded":
                maturity = min(maturity, 1)

        # A justification for the controls no risk names. Withheld for a few
        # of them in the `gaps` profile, which is what leaves the SoA with
        # rows an auditor would ask about.
        if applicable and justification is None:
            baseline = BASELINE_JUSTIFICATIONS.get(control.capability)
            withhold = profile == "gaps" and rng.random() < 0.18
            justification = None if withhold else baseline

        states.append(
            ControlState(
                control_id=control.id,
                applicable=applicable,
                justification=justification,
                status=status,
                owner_role=owner or None,
                maturity=0 if status in ("not_started", "excluded") else maturity,
                implemented_on=implemented_on,
                last_reviewed=TODAY - dt.timedelta(days=rng.randint(10, 300)),
                notes=None,
            )
        )
    return states


def _requirement_states(profile: Profile, catalogue: Catalogue) -> list[RequirementState]:
    states: list[RequirementState] = []
    for requirement in catalogue.requirements:
        unmet = profile == "gaps" and requirement.id in (
            *UNMET_MANDATORY_REQUIREMENTS,
            UNMET_OPTIONAL_REQUIREMENT,
        )
        if unmet:
            states.append(
                RequirementState(
                    requirement_id=requirement.id,
                    status="in_progress" if requirement.id == "C.8.3" else "not_started",
                    owner_role="Information Security Officer",
                    completed_on=None,
                    notes=None,
                )
            )
        else:
            states.append(
                RequirementState(
                    requirement_id=requirement.id,
                    status="met",
                    owner_role="Information Security Officer",
                    completed_on=TODAY - dt.timedelta(days=45),
                    notes=None,
                )
            )
    return states


def _evidence(
    profile: Profile, catalogue: Catalogue, states: list[ControlState], rng: random.Random
) -> list[Evidence]:
    """One or two artefacts per implemented control, aged as the profile requires."""
    items: list[Evidence] = []
    by_id = {state.control_id: state for state in states}
    for control in catalogue.controls:
        state = by_id.get(control.id)
        if state is None or not state.is_implemented:
            continue
        if profile == "gaps" and control.id in NO_EVIDENCE_CONTROLS:
            continue

        window = control.evidence_valid_days
        if profile == "gaps" and control.id in STALE_EVIDENCE_CONTROLS:
            age = window + rng.randint(40, 200)
        else:
            # Comfortably inside the window, so the demo does not flicker
            # between runs on different days.
            age = rng.randint(5, max(6, window // 3))

        kind = control.evidence_expected[0]
        if profile == "gaps" and control.id == WRONG_EVIDENCE_KIND_CONTROL:
            kind = "screenshot"

        label = f"{control.id}-{kind}"
        items.append(
            Evidence(
                subject_kind="control",
                subject_id=control.id,
                title=f"{control.title} -- {kind}",
                kind=kind,
                collected_on=TODAY - dt.timedelta(days=age),
                collected_by=state.owner_role or "Information Security Officer",
                location=f"evidence/{control.id.lower().replace('.', '-')}/{kind}.pdf",
                content_hash=_hash_for(label),
                valid_days=None,
                superseded_on=None,
            )
        )
        if rng.random() < 0.35:
            second = control.evidence_expected[-1]
            items.append(
                Evidence(
                    subject_kind="control",
                    subject_id=control.id,
                    title=f"{control.title} -- {second}",
                    kind=second,
                    collected_on=TODAY - dt.timedelta(days=age + rng.randint(1, 30)),
                    collected_by=state.owner_role or "Information Security Officer",
                    location=f"evidence/{control.id.lower().replace('.', '-')}/{second}.pdf",
                    content_hash=_hash_for(f"{label}-2"),
                    valid_days=None,
                    superseded_on=None,
                )
            )
    return items


def _acceptances(profile: Profile, risks: list[Risk], settings: Settings) -> list[Acceptance]:
    """Signed acceptances for the risks that are not treated further.

    In ``gaps`` three of them are wrong in ways that every real register gets
    wrong eventually: one signed against a score the risk no longer has, one
    that expired, and one signed by a manager for a risk that needed top
    management.
    """
    by_id = {risk.risk_id: risk for risk in risks}
    acceptances: list[Acceptance] = []

    if profile == "gaps":
        rescored = by_id[RISK_RESCORED_AFTER_ACCEPTANCE]
        acceptances.append(
            Acceptance(
                risk_id=rescored.risk_id,
                accepted_by="Claire Fontaine",
                accepted_role="Operations Director",
                rationale=(
                    "A UPS replacement is scheduled for the next budget cycle; the residual "
                    "exposure was judged acceptable until then."
                ),
                # Signed for 4, and the risk now stands at 8.
                residual_at_acceptance=4,
                accepted_on=TODAY - dt.timedelta(days=210),
                expires_on=TODAY + dt.timedelta(days=150),
            )
        )
        expired = by_id[RISK_WITH_EXPIRED_ACCEPTANCE]
        acceptances.append(
            Acceptance(
                risk_id=expired.risk_id,
                accepted_by="Marc Delaunay",
                accepted_role="Head of IT",
                rationale=(
                    "Restore testing was deferred while the backup vault migration ran; "
                    "accepted for one quarter."
                ),
                residual_at_acceptance=expired.effective_score,
                accepted_on=TODAY - dt.timedelta(days=300),
                expires_on=TODAY - dt.timedelta(days=30),
            )
        )
        too_low = by_id[RISK_ACCEPTED_TOO_LOW]
        acceptances.append(
            Acceptance(
                risk_id=too_low.risk_id,
                accepted_by="Marc Delaunay",
                accepted_role="Head of IT",
                rationale=(
                    "Vulnerability scanning is being procured; the exposure is accepted "
                    "until the tooling is in place."
                ),
                residual_at_acceptance=too_low.effective_score,
                accepted_on=TODAY - dt.timedelta(days=60),
                expires_on=TODAY + dt.timedelta(days=120),
            )
        )
        return acceptances

    # The remediated profile: whatever still sits above appetite carries a
    # current signature, at the right level.
    for risk in risks:
        if not risk.above_appetite(settings):
            continue
        role = (
            "Managing Director (top management)"
            if risk.needs_management_signature(settings)
            else "Information Security Officer"
        )
        acceptances.append(
            Acceptance(
                risk_id=risk.risk_id,
                accepted_by="Hélène Rousseau" if "Managing" in role else "Yanis Belkacem",
                accepted_role=role,
                rationale=(
                    "Treated to the lowest level the current architecture supports; the "
                    "remaining exposure is accepted and reviewed each quarter."
                ),
                residual_at_acceptance=risk.effective_score,
                accepted_on=TODAY - dt.timedelta(days=20),
                expires_on=TODAY + dt.timedelta(days=345),
            )
        )
    return acceptances


def generate(
    profile: Profile = "gaps",
    settings: Settings | None = None,
    catalogue: Catalogue | None = None,
) -> DemoReport:
    """Populate the registers with the simulated organisation."""
    settings = settings or get_settings()
    catalogue = catalogue or get_catalogue(settings)
    if profile not in ("gaps", "ready"):
        raise ValueError(f"unknown profile {profile!r}; expected 'gaps' or 'ready'")
    rng = random.Random(settings.random_seed)

    assets = _assets()
    risks = _risks(profile, rng)
    states = _control_states(profile, catalogue, rng)
    requirements = _requirement_states(profile, catalogue)
    evidence = _evidence(profile, catalogue, states, rng)

    upsert_assets(assets, settings)
    upsert_risks(risks, settings)
    upsert_control_states(states, settings)
    upsert_requirement_states(requirements, settings)
    record_evidence(evidence, settings)

    for acceptance in _acceptances(profile, risks, settings):
        accept_risk(acceptance, settings)

    # Clause 9. In `gaps` the audit is stale and the review has never happened,
    # which is the most common reason a first certification attempt fails.
    if profile == "gaps":
        record_activity(
            "internal_audit",
            TODAY - dt.timedelta(days=430),
            "Cabinet Ardent (external auditor, acting internally)",
            "First internal audit of the ISMS; 9 nonconformities raised, 6 closed since.",
            covers=["A.5", "A.6"],
            findings_raised=9,
            settings=settings,
        )
    else:
        record_activity(
            "internal_audit",
            TODAY - dt.timedelta(days=75),
            "Cabinet Ardent (external auditor, acting internally)",
            "Full-scope internal audit; 3 minor nonconformities raised and closed.",
            covers=["A.5", "A.6", "A.7", "A.8", "clauses 4-10"],
            findings_raised=3,
            settings=settings,
        )
        record_activity(
            "management_review",
            TODAY - dt.timedelta(days=40),
            "Hélène Rousseau (Managing Director)",
            (
                "Reviewed audit results, the risk register, objectives and resourcing; "
                "approved the treatment plan for the coming year."
            ),
            covers=["clause 9.3"],
            settings=settings,
        )
        record_activity(
            "risk_review",
            TODAY - dt.timedelta(days=30),
            "Yanis Belkacem (Information Security Officer)",
            "Quarterly re-scoring of the register with the asset owners.",
            settings=settings,
        )

    report = DemoReport(
        profile=profile,
        assets=len(assets),
        risks=len(risks),
        controls_decided=len(states),
        evidence_items=len(evidence),
        requirements=len(requirements),
        planted_defects=PLANTED if profile == "gaps" else [],
    )
    logger.info("demo organisation generated", extra=report.as_dict())
    return report
