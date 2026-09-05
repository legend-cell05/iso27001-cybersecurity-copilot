# The catalogue

> **Synthetic data, and no text of the standard.** This repository references
> ISO/IEC 27001:2022 control and clause *identifiers*. Every title, intent and
> evidence rule in the catalogue is written for this project. Using it for a
> real certification requires a licensed copy of ISO/IEC 27001 and
> ISO/IEC 27002.

---

## 1. The copyright position, stated plainly

ISO standards are copyrighted works, sold by ISO and its national member
bodies. A GRC tool on GitHub cannot ship their text, and a great many of them
quietly do.

This one does not. What it contains is:

| In the repository | Not in the repository |
| --- | --- |
| Control identifiers (`A.5.1` … `A.8.34`) and clause numbers (4 to 10) | The standard's control titles |
| Titles written for this project | The standard's control text |
| One- or two-sentence intents written for this project | Implementation guidance from ISO/IEC 27002 |
| Evidence expectations and freshness windows chosen for this project | The standard's attribute tables |

Identifiers are references, the way a citation is a reference: `A.8.13` points
at a control in a document the reader is expected to have. The wording beside
it is mine, and it is deliberately a *paraphrase of the subject matter* rather
than a close restatement — the intent of `A.8.13` here says that backups are
taken *and restored on a schedule to prove they work*, which is the useful part
and is not how the standard puts it.

CI enforces the position twice: the catalogue manifest must carry the statement
above, and no control intent may read like standard text (a tripwire on
"shall"-style phrasing). Neither check can prove the absence of copying; both
make an accidental paste visible.

**What this costs.** An organisation using this tool still needs the standard.
The catalogue tells it *which* controls exist and what this project takes them
to mean; only the standard tells it what the auditor will actually read out.

---

## 2. Shape

```
catalogue/
├── catalogue.yaml              manifest: version, vocabularies, file list
├── clauses.yaml                the management-system requirements, 4 to 10
└── controls/
    ├── 05_organizational.yaml  A.5.1  – A.5.37   (37)
    ├── 06_people.yaml          A.6.1  – A.6.8    (8)
    ├── 07_physical.yaml        A.7.1  – A.7.14   (14)
    └── 08_technological.yaml   A.8.1  – A.8.34   (34)
```

93 controls and 25 clause requirements, of which 22 are marked mandatory for
certification.

One control:

```yaml
- id: A.8.13
  title: Information backup
  intent: >-
    Backups are taken according to an agreed policy and -- the part that is
    usually missing -- restored on a schedule to prove they work.
  control_types: [corrective]
  properties: [I, A]
  function: recover
  capability: continuity
  evidence_expected: [record, report]
  evidence_valid_days: 180
```

---

## 3. Why the catalogue is data and not a table

It could have been a database table that an administrator edits. It is
versioned YAML instead, for one reason: **what the controls are** changes with
a release of this software, while **what the organisation has done about them**
changes every week. Mixing the two in one table leaves a tool unable to
distinguish an operator's edit from a revision of the standard, which is
exactly the distinction an auditor asks about.

So the catalogue ships with the code, in the container's read-only layer, and
the organisation's decisions live in PostgreSQL. `control_state.control_id` is
deliberately **not** a foreign key: the catalogue is not a table to point at.
Instead, every assessment checks the identifiers the registers use against the
catalogue that is loaded, and reports the ones that no longer exist
(`RISK_NAMES_UNKNOWN_CONTROL`).

---

## 4. Closed vocabularies

The manifest defines every value the control files may use: themes, control
types, properties, functions, capabilities and evidence kinds. A control naming
anything else fails to load.

That is a deliberate trade. Adding a capability means editing the manifest,
which is friction. The friction buys the property that a Statement of
Applicability can never contain a category nobody can explain — a failure that
otherwise surfaces in front of the auditor rather than at load time.

The five **functions** (identify, protect, detect, respond, recover) are the
NIST Cybersecurity Framework's, used because they are a widely understood
vocabulary for what a control is *for*, and because they are not ISO's.

The sixteen **capabilities** are this project's own. They exist because
"38% of technological controls" is less useful to a security manager than
"identity and access is the weakest capability in the ISMS", and the posture
report aggregates by them.

---

## 5. The fingerprint

Every assessment records two things about the catalogue: its `version`, and a
SHA-256 over the bytes of every catalogue file, in a fixed order, including
each file's name.

The version is what a human quotes. The fingerprint is what a machine compares,
because two catalogues can carry the same version number and different
content — somebody edited a file and did not bump it. A test asserts exactly
this: edit one control's title, leave the version alone, and the fingerprint
changes anyway.

That is what lets a Statement of Applicability sent to an auditor in March be
matched, in September, to the catalogue that produced it.

---

## 6. Evidence expectations

Each control carries the kinds of artefact that would support it, and how long
one stays current:

| Control | Expected | Valid for | Why that long |
| --- | --- | --- | --- |
| `A.5.1` policy set | policy, record | 365 days | an approved policy is reviewed annually |
| `A.5.18` access rights | record, report | 180 days | an access review from a year ago describes a different population |
| `A.8.7` malware protection | screenshot, report | 90 days | a configuration screenshot from a year ago says nothing about today |
| `A.8.15` logging | log, screenshot | 90 days | the same |
| `A.8.13` backup | record, report | 180 days | a restore test's usefulness decays fast |

The window is per control on purpose. A single global "evidence expires after a
year" is the setting every GRC spreadsheet uses and it is wrong in both
directions at once: too slack for a firewall screenshot, needlessly strict for
a signed policy.

`SENTINEL_EVIDENCE_MAX_AGE_DAYS` is a ceiling over the top, not an override: the
**stricter** of the two applies, so a global setting can tighten a control's
window and never loosen it.

---

## 7. Adding or changing a control

1. Edit the theme file under `catalogue/controls/`.
2. Keep the wording your own.
3. Use only values the manifest defines.
4. Bump `version` in `catalogue.yaml`.
5. Run `sentinel catalogue` and the catalogue tests.

Step 4 is the one people skip, and the fingerprint is the reason it does not
matter as much as it would otherwise: the assessment records what actually ran
either way.
