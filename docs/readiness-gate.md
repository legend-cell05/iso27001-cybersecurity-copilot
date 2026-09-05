# The audit-readiness gate

> Synthetic data, and no deployment. "Ready" here means the registers satisfy
> seven checks, not that a certification body has said anything.

A certification audit is not something to walk into hopefully. The gate exists
to be able to say **no**, with the number that produced the refusal and the bar
it was measured against — because a refusal that cannot be argued with gets
overridden rather than fixed.

"Not ready" is useless. This is not:

```
implementation_ratio: 83 of 89 applicable controls implemented (93.3%), bar 95%
```

---

## 1. The seven checks

| # | Check | Passes when | Configured by |
| --- | --- | --- | --- |
| 1 | `every_control_assessed` | all 93 Annex A controls carry an applicability decision | — |
| 2 | `mandatory_clauses_met` | every requirement marked mandatory for certification is met | the catalogue |
| 3 | `implementation_ratio` | implemented ÷ applicable ≥ 0.95 | `SENTINEL_MIN_IMPLEMENTED_RATIO` |
| 4 | `implemented_controls_evidenced` | controls claimed as implemented with no evidence ≤ 0 | `SENTINEL_MAX_CONTROLS_WITHOUT_EVIDENCE` |
| 5 | `risks_treated_or_accepted` | no risk above appetite lacks an acceptance that covers its current score | `SENTINEL_RISK_APPETITE` |
| 6 | `internal_audit_and_review` | both have happened, within a year | `SENTINEL_REQUIRE_INTERNAL_AUDIT` |
| 7 | `no_blocking_findings` | the catch-all | — |

Check 7 is deliberately last and deliberately separate. The six before it are
things a security manager can act on directly; the seventh catches a blocking
finding that no individual check happens to cover, so that adding a rule cannot
quietly widen what the gate lets through.

---

## 2. A skipped check is never a pass

`SENTINEL_REQUIRE_INTERNAL_AUDIT=false` turns check 6 off, so that the gate can
be run early in a programme without pretending an audit happened. It is then
recorded as **skipped**, with its own wording:

```
internal_audit_and_review  skip  not required by configuration --
SENTINEL_REQUIRE_INTERNAL_AUDIT is off, so this says nothing about clause 9
```

A gate that reports seven passes when it ran six is a gate nobody should trust,
and the `readiness_check` table has a `CHECK` constraint that a skipped row is
never recorded as failed.

---

## 3. Both verdicts, on the demo data

The demo ships two profiles precisely so that both directions are demonstrated
rather than asserted. A gate that has never passed is as untrustworthy as one
that has never refused.

**`gaps`** — an ISMS nine months into a programme. All seven checks fail:

| Check | Detail |
| --- | --- |
| `every_control_assessed` | 92 of 93 controls carry an applicability decision; 1 does not |
| `mandatory_clauses_met` | 19 of 22 met; outstanding: C.6.2, C.8.3, C.9.3 |
| `implementation_ratio` | 83 of 89 implemented (93.3%), bar 95% |
| `implemented_controls_evidenced` | 2 implemented controls have no evidence, tolerance 0 |
| `risks_treated_or_accepted` | 9 risks above the appetite of 6 carry no acceptance that covers their current score |
| `internal_audit_and_review` | internal audit 2025-07-24; no management review |
| `no_blocking_findings` | 11 blocking findings across 7 rules |

```console
$ sentinel readiness ; echo "exit=$?"
NOT READY ...
exit=3
```

**`ready`** — the same organisation after remediation. All seven pass:

| Check | Detail |
| --- | --- |
| `every_control_assessed` | 93 of 93 |
| `mandatory_clauses_met` | 22 of 22 |
| `implementation_ratio` | 92 of 92 implemented (100.0%), bar 95% |
| `implemented_controls_evidenced` | 0 unevidenced, tolerance 0 |
| `risks_treated_or_accepted` | every risk within appetite or covered by a matching acceptance |
| `internal_audit_and_review` | internal audit 2026-07-14; management review 2026-08-18 |
| `no_blocking_findings` | none |

---

## 4. Exit codes, and why 3

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | handled failure — the database is unreachable, the catalogue will not load |
| 2 | misuse — an unknown profile, a malformed date |
| **3** | **refused by the gate** |

A refused gate is the tool working. A pipeline that runs `sentinel readiness`
nightly has to tell "the ISMS is not ready" apart from "the tool crashed"
without parsing text, and a team that cannot tell them apart stops reading
either.

The API says the same thing in its own vocabulary: `GET /api/v1/readiness`
answers **200** when the gate passes and **409** when it refuses, so a
monitoring check can be a URL.

---

## 5. The override

`--force` exists. A controlled exception is sometimes right: a stage 1 audit is
booked, the board has accepted the remaining gaps in writing, the programme
proceeds anyway. What it must never be is accidental, so it:

- is a named flag, never a default or an environment variable;
- **requires a reason** — forcing without one is itself refused, with exit
  code 3;
- writes the reason onto the assessment row, where the audit dossier prints it
  under the verdict:

> The readiness gate **refused**, and readiness was declared anyway with an
> explicit override. Reason recorded: *Stage 1 audit booked; the board accepted
> the remaining gaps in writing on 2026-09-20.*

A gate with no override is routed around by somebody editing a spreadsheet. A
gate whose override is recorded keeps the exception inside the system, where
the auditor can see it too.

---

## 6. What the gate cannot tell you

Worth saying, because an interview will ask:

- **It checks records, not reality.** An organisation with beautifully
  evidenced controls that nobody operates passes every check here. Only a human
  auditor sampling the actual practice can catch that, which is what a
  certification audit is for.
- **It does not judge the quality of evidence.** A screenshot of a policy nobody
  follows counts as a policy.
- **It does not check the scope.** A scope narrow enough to exclude everything
  difficult will pass; the standard's own answer to that is the auditor.
- **It has no opinion about the risk scores themselves.** Likelihood 2 on a
  risk that is obviously a 5 produces a clean register.
