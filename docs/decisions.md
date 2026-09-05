# Decisions

Architecture decision records: the choice, the reason, what it costs, and **the
condition under which it becomes the wrong choice** — the last being the part
that makes an ADR worth writing.

Status: all `Accepted` unless noted.

---

## ADR-001 — No language model anywhere in the engine

**Decision.** Every finding, score and verdict is produced by deterministic
code. No LLM writes policies, explains gaps or judges applicability.

**Why.** A compliance finding has to be reproducible and arguable. An auditor
asking "why did your tool say this?" needs an answer that is the same today as
it was in March, and that a person can disagree with by pointing at a rule.
Generated prose fails both tests: it varies between runs, and an organisation
cannot argue with it — so it gets ignored, which is worse than not having it.
There is also a practical constraint that a portfolio project should be honest
about: an LLM layer needs an API key that cannot be committed, and CI could not
test it, so the one part of the system that would be hardest to trust would
also be the only part with no test.

**Cost.** No drafting help. Writing an exclusion justification is still a human
sentence, and the tool only checks that one exists and is not twelve characters
long.

**Wrong when.** The task changes from *judging* to *drafting* — turning an
approved decision into prose for a document — and the output is reviewed by a
human before it counts. Even then the judgement stays here.

---

## ADR-002 — The catalogue is versioned data shipped with the code

**Decision.** 93 controls and 25 clause requirements in YAML under
`catalogue/`, loaded through a closed schema, fingerprinted, copied into the
container as root-owned files.

**Why.** What the controls *are* changes with a release of this software; what
the organisation has *done about them* changes every week. A single table
holding both leaves the tool unable to distinguish an operator's edit from a
revision of the standard — exactly the distinction an audit asks about.

**Cost.** Changing a control is a deployment, not an afternoon's editing.

**Wrong when.** An organisation needs to extend the catalogue with its own
controls at run time — a real requirement for anyone mapping to SOC 2 or NIS2
alongside. The answer would be a second, additive catalogue source, not moving
this one into the database.

**Rejected alternative.** A `control` table with a foreign key from
`control_state`. It looks tidier and it makes the control set editable by
anyone with a database connection.

---

## ADR-003 — No text of the standard is reproduced

**Decision.** The repository carries ISO/IEC 27001:2022 *identifiers* and
wording written for this project. CI checks the manifest states this and that
no control intent reads like standard text.

**Why.** ISO standards are copyrighted and sold. A public GRC repository that
ships their control text is infringing, and a great many do it quietly.

**Cost.** The catalogue's titles are not the official ones, so an auditor
reading both will see different words, and this tool cannot be the
organisation's only copy of anything.

**Wrong when.** Never, without a licence. With one, the honest change is a
private catalogue overlay that carries the real text and is not published.

---

## ADR-004 — The rules are pure functions of a snapshot

**Decision.** Every rule takes `(RegisterSnapshot, Settings)` and returns
findings. A rule cannot query the database.

**Why.** Testability first: the unit suite builds a snapshot by hand, breaks
one thing and asserts one rule fires — no database, milliseconds. Consistency
second: the same rule cannot behave differently between the CLI and the API.
Correctness third: an assessment is a single consistent read, so a risk cannot
be re-scored halfway through the assessment that is judging it.

**Cost.** Everything is loaded whether a given rule needs it or not, and a rule
that wanted one extra fact requires the snapshot to grow.

**Wrong when.** The register grows past what fits comfortably in memory — tens
of thousands of risks. At that size the rules would need to become queries, and
the test suite would need a database.

---

## ADR-005 — `today` is an argument, not a call to `date.today()`

**Decision.** The assessment takes the date it is being run for.

**Why.** Ages are the whole point of several rules — evidence freshness,
acceptance expiry, audit intervals. Passing the date lets a test assert the
property that matters most about an ISMS and is hardest to demonstrate: it
decays by doing nothing. The same registers, four hundred days later, stop
being ready.

**Cost.** One more argument to thread through.

**Wrong when.** Never. The only risk is somebody passing a date to make a
report look better, and the assessment records the date it used.

---

## ADR-006 — Residual risk counts only implemented controls

**Decision.** A residual score whose controls are not implemented produces a
finding rather than being accepted as a number.

**Why.** It is the most common comfortable fiction in a GRC tool: every risk
turns green the moment a treatment plan exists. The register then reports a
state the organisation has not reached.

**Cost.** An organisation mid-programme sees a worse picture than its
spreadsheet showed it, and that is the point, but it is not a popular feature.

**Wrong when.** A control is implemented in a way the tool cannot see — an
inherited control at a cloud provider, say. The answer is to record it as
implemented with the provider's attestation as evidence, not to relax the rule.

---

## ADR-007 — Risk appetite and the gate thresholds are configuration

**Decision.** Appetite, escalation level, implementation bar, evidence
tolerance and the clause 9 requirement are all environment settings.

**Why.** Every one of them is a business decision that someone signs. A
constant buried in a scoring function cannot be argued with in a management
review, and it will be — appetite is exactly the thing management is supposed
to own.

**Cost.** Two organisations' assessments are not comparable unless their
settings match, which is why every assessment freezes the settings it ran under
into `assessment.parameters`.

**Wrong when.** Never, though a real deployment would want them versioned and
approved rather than living in a `.env`.

---

## ADR-008 — An acceptance records the score it was signed against

**Decision.** `risk_acceptance.residual_at_acceptance`, compared against the
risk's current score on every assessment.

**Why.** A signature for a residual score of 6 is not a signature for 15. Every
real register drifts this way, because re-scoring happens in a quarterly review
and the acceptances live somewhere else. Nothing in a spreadsheet notices, and
the organisation believes a risk is accepted when it is not.

**Cost.** Re-scoring a risk upward invalidates its acceptance and produces a
blocking finding, which means work — getting the signature again.

**Wrong when.** Never for an upward re-score. A downward one is already
handled: an acceptance covers any score at or below what was signed.

---

## ADR-009 — One acceptance in force per risk, enforced by an index

**Decision.** A partial unique index on `(risk_id) WHERE withdrawn_on IS NULL`;
re-accepting withdraws the previous one in the same transaction and keeps the
row.

**Why.** "The application only ever writes one" is a hope about every code
path, every manual fix and every interrupted run. An index is an invariant.
Keeping the superseded row matters separately: "who accepted this, when, and
who changed their mind" is a question that gets asked.

**Cost.** Two statements instead of one on the write path.

**Wrong when.** A risk legitimately needs two concurrent acceptances — two
business units accepting their own share. That would be a different model.

---

## ADR-010 — Exit code 3 means "not ready", not "broken"

**Decision.** 0 success, 1 handled failure, 2 misuse, 3 refused by the gate.
The API mirrors it with 200 and 409.

**Why.** A refused gate is the system working. A pipeline that cannot tell it
apart from a crash either pages someone every night or stops paging at all, and
the second happens first.

**Cost.** One more convention to document.

**Wrong when.** Never, for a CLI meant to be scheduled.

---

## ADR-011 — `--force` exists and requires a reason

**Decision.** Readiness can be declared over a refused gate, only with an
explicit flag and a written reason, which is recorded on the assessment and
printed in the dossier.

**Why.** A gate with no override is routed around by somebody editing the
spreadsheet, and then nothing is recorded at all. A recorded override keeps the
exception inside the system.

**Cost.** It can be used badly. Visibly, which is the design: the dossier
prints the reason under the verdict.

**Wrong when.** Certification decisions become automated, at which point the
override belongs to an approval workflow with an identity attached — and this
system has no identities.

---

## ADR-012 — A skipped gate check is never reported as a pass

**Decision.** `readiness_check.skipped`, printed as "skip", with a `CHECK`
constraint that a skipped row cannot be recorded as failed.

**Why.** `SENTINEL_REQUIRE_INTERNAL_AUDIT=false` is a legitimate setting early
in a programme. A gate that then reports seven passes is lying by omission, and
the reader has no way to know.

**Cost.** The summary has to explain itself: "5 checks passed, 1 skipped".

**Wrong when.** Never.

---

## ADR-013 — The SoA is built by walking the catalogue

**Decision.** The Statement of Applicability is generated from the catalogue,
not from the rows in `control_state`. A control with no decision appears in the
document as an explicit gap.

**Why.** A document built from what somebody remembered to fill in cannot tell
you what was forgotten — and "forgot to decide about a control" is the single
most common reason an SoA is sent back.

**Cost.** The document is uncomfortable to read mid-programme, and it prints
"**not decided**" in a table an auditor will see.

**Wrong when.** Never.

---

## ADR-014 — Evidence freshness is per control, with a global ceiling

**Decision.** Each control carries `evidence_valid_days`;
`SENTINEL_EVIDENCE_MAX_AGE_DAYS` is a ceiling, and the **stricter** of the two
applies.

**Why.** A single global window is wrong in both directions at once: too slack
for a malware-protection screenshot (90 days here), needlessly strict for an
approved policy (365). Making the global setting a ceiling rather than an
override means an organisation can tighten everything without accidentally
loosening the controls that need to age fastest.

**Cost.** 93 numbers to justify, which is why they are in the catalogue with
the rest of the reasoning.

**Wrong when.** An organisation's own policy is looser than the catalogue for a
specific control. Today that requires editing the catalogue; a per-control
override in the database would be the honest fix.

---

## ADR-015 — The API is read-only and says so

**Decision.** Six GET endpoints. Nothing mutates.

**Why.** There is no authentication (see `docs/security.md`). Offering
"change an applicability decision" over an unauthenticated endpoint would be an
odd thing for a compliance tool to do, and adding authentication properly is
more than a portfolio project can honestly claim to have done.

**Cost.** The API cannot drive a UI that edits the register.

**Wrong when.** Authentication and an audit log of who changed what exist. Both
are named in the security document as missing.

---

## ADR-016 — The tool never edits the registers it judges

**Decision.** The assessment reads the registers and writes findings,
assessments and exports. It never updates a control state or a risk score.

**Why.** A tool that silently fixes its own findings is a tool whose findings
mean nothing, and an ISMS whose records were written by the compliance tool
rather than by the people doing the work is not evidence of anything.

**Cost.** No "mark all reviewed" button.

**Wrong when.** Never, for findings. A future bulk-edit command would be a
separate, explicitly named operation with its own record.

---

## ADR-017 — Clause requirements are modelled, not just Annex A

**Decision.** 25 requirements from clauses 4 to 10, 22 of them mandatory for
certification, blocking the gate when unmet.

**Why.** Annex A gets the attention and the certificate is granted against the
clauses. An organisation can implement all 93 controls and fail its stage 2
audit for having no management review — and a tool that tracks only Annex A
will tell it everything is fine, which is the most expensive way for a
compliance tool to be wrong.

**Cost.** More to record, and the clause requirements are coarser than the
controls: "leadership demonstrated" is one row with a status.

**Wrong when.** The tool is used only as a control tracker alongside something
else that owns the management system — in which case
`SENTINEL_REQUIRE_INTERNAL_AUDIT=false` and the clause rows can sit at
"not_started" without the gate being consulted.

---

## ADR-018 — Documents are hashed and recorded, and deterministic

**Decision.** Every export is written, hashed with SHA-256, and recorded
against the assessment that produced it. Nothing writes a generation timestamp
into a document body.

**Why.** An auditor holding a six-month-old SoA needs to ask *which state of
the ISMS produced this?* and get an answer. Determinism is what makes the hash
worth anything: two exports that differ mean the ISMS differed, not that the
tool felt differently on a Tuesday.

**Cost.** The document cannot say "generated at 14:32", which people ask for.
The assessment it names already carries the timestamp.

**Wrong when.** Never.
