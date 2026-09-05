# The finding rules

> Synthetic data. Every figure below was measured on the simulated
> organisation produced by `sentinel demo --profile gaps`.

Twenty-two rules. Each one is a pure function of the registers, each names
itself in its output, and each says what to do about what it found.

That last part is enforced by the database — `finding.remediation` has a
`CHECK` that it is not blank — because a finding with no remediation is a
complaint, and a report full of complaints gets filed rather than worked.

---

## 1. Severity

| Severity | Meaning | Effect |
| --- | --- | --- |
| `blocking` | fails a certification audit on the day | the readiness gate refuses |
| `high` | would very likely become a major nonconformity | reported, does not block |
| `medium` | a minor nonconformity or an observation | reported |
| `low` | real, not urgent | reported |
| `info` | worth knowing, not wrong | reported |

Findings come back worst first, and every severity is reported even at zero: a
summary whose "blocking" line disappears when there are none reads as though
the check was not run.

---

## 2. The rules

### Applicability and implementation

| Rule | Severity | Fires when |
| --- | --- | --- |
| `CONTROL_NOT_ASSESSED` | blocking | a control has no applicability decision at all — the SoA has a hole in it |
| `CONTROL_NOT_IMPLEMENTED` | blocking / high | applicable and not implemented; blocking when it has not been started at all, high when it is planned or in progress |
| `CONTROL_WITHOUT_OWNER` | medium | implemented, with no owning role |
| `CONTROL_IMPLEMENTED_AT_AD_HOC_MATURITY` | low | implemented at maturity 0 or 1 — it happens when someone remembers |
| `EXCLUSION_WEAKLY_JUSTIFIED` | medium | excluded with under 20 characters of justification |
| `EXCLUDED_CONTROL_TREATS_A_RISK` | high | excluded, and named as treatment by a risk in the register |

`EXCLUDED_CONTROL_TREATS_A_RISK` is the contradiction worth naming: the
organisation has said both that it needs the control and that the control does
not apply. Both statements cannot be true, and the SoA is where an auditor will
notice.

### Evidence

| Rule | Severity | Fires when |
| --- | --- | --- |
| `CONTROL_WITHOUT_EVIDENCE` | blocking | claimed as implemented with nothing on file |
| `EVIDENCE_STALE` | high | the newest artefact is past the control's freshness window |
| `EVIDENCE_EXPIRING` | low | it expires within the warning period (60 days by default) |
| `EVIDENCE_KIND_MISMATCH` | medium | evidence exists, but none of the kind the control calls for |

An implemented control with no evidence is an assertion, which is why it
blocks. `EVIDENCE_KIND_MISMATCH` exists because a screenshot is not a
substitute for an approved policy, and a count of artefacts hides that.

### Risk

| Rule | Severity | Fires when |
| --- | --- | --- |
| `RISK_NAMES_UNKNOWN_CONTROL` | high | a risk names a control identifier the catalogue does not contain |
| `RISK_MODIFIED_BY_NOTHING` | high | treatment is "modify" and no control is named |
| `RISK_TREATED_BY_UNIMPLEMENTED_CONTROL` | blocking / high | a residual score rests on controls that are not implemented |
| `RISK_ABOVE_APPETITE_UNACCEPTED` | blocking / high | above appetite with no acceptance on file; blocking at or above the escalation level |
| `ACCEPTANCE_STALE_AFTER_RESCORING` | blocking | the risk was re-scored above the number the acceptance was signed for |
| `ACCEPTANCE_EXPIRED` | high | the acceptance has passed its expiry date |
| `HIGH_RISK_ACCEPTED_BELOW_MANAGEMENT` | high | a risk at or above the escalation level was accepted by someone below top management |
| `RISK_REVIEW_OVERDUE` | medium | the review date has passed |
| `APPLICABLE_CONTROLS_TRACE_TO_NO_RISK` | info | applicable controls that neither treat a risk nor carry a justification |

Two of these are the reason the project exists.

**`RISK_TREATED_BY_UNIMPLEMENTED_CONTROL`** is how a register full of green
residual scores turns out to be a plan rather than a state. The risk claims a
reduced score; the controls it relies on are still "planned". The score is a
forecast, and the tool says so instead of averaging it into a dashboard.

**`ACCEPTANCE_STALE_AFTER_RESCORING`** is the one nobody writes by hand. A risk
accepted at a residual score of 6 and later re-scored to 15 is not an accepted
risk; it carries a signature for a different number. Every real register drifts
this way, because re-scoring happens in a quarterly review and acceptances are
filed somewhere else entirely. Nothing in a spreadsheet notices.

### Management system

| Rule | Severity | Fires when |
| --- | --- | --- |
| `CLAUSE_REQUIREMENT_UNMET` | blocking / medium | a clause 4-10 requirement is unmet; blocking when it is mandatory for certification |
| `MANAGEMENT_ACTIVITY_NEVER_PERFORMED` | blocking | no internal audit, or no management review, has ever been recorded |
| `MANAGEMENT_ACTIVITY_OVERDUE` | high | the last one was more than a year ago |

Annex A gets the attention; the certificate is granted against the clauses. An
organisation can implement all 93 controls and fail its stage 2 audit for
having no management review, and a tool that tracks only Annex A will tell it
everything is fine.

---

## 3. What the demo produces

`sentinel demo --profile gaps` plants 21 defects on purpose
(`docs/demo.md` lists them). The engine finds 44 findings from 21 rules:

| Severity | Count |
| --- | --- |
| blocking | 11 |
| high | 23 |
| medium | 7 |
| low | 2 |
| info | 1 |

The heaviest rules on that data:

| Rule | Count |
| --- | --- |
| `RISK_ABOVE_APPETITE_UNACCEPTED` | 7 |
| `CONTROL_NOT_IMPLEMENTED` | 6 |
| `RISK_TREATED_BY_UNIMPLEMENTED_CONTROL` | 5 |
| `CLAUSE_REQUIREMENT_UNMET` | 4 |
| `CONTROL_WITHOUT_EVIDENCE`, `EVIDENCE_STALE`, `CONTROL_WITHOUT_OWNER`, `RISK_REVIEW_OVERDUE`, `CONTROL_IMPLEMENTED_AT_AD_HOC_MATURITY` | 2 each |

After remediation (`--profile ready`) the same engine produces **one** finding,
`EVIDENCE_EXPIRING`, which is a warning that something is due rather than a
gap — and the gate passes.

---

## 4. Adding a rule

1. Write a function in `assessment/rules.py` taking `(snapshot, settings)`.
2. Give the finding a rule name, a severity, a subject, a detail with the
   numbers in it, and a remediation.
3. Add it to `RULES`.
4. Write the test: build a clean snapshot, break exactly one thing, assert that
   your rule and only your rule fires.

Step 4 is where the value is. The first assertion in `tests/unit/test_rules.py`
is that a complete ISMS produces *no* findings at all — every other test in the
file depends on it, and a new rule that fires on a clean register breaks it
immediately.
