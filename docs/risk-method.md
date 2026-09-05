# The risk method

> Synthetic data. The register described here is the simulated one produced by
> `sentinel demo`.

ISO 27001 requires a documented risk assessment process that produces
consistent, valid and comparable results (clause 6.1.2), and leaves the method
to the organisation. This is the method this project implements, and the
reasoning behind each choice — which is the part an auditor asks about.

---

## 1. Scales

Likelihood and impact are scored **1 to 5**, and the risk is their product, so
the scale runs 1 to 25.

Five points rather than three, because three collapses every interesting
distinction into "medium". Five rather than ten, because nobody can defend the
difference between a 6 and a 7 in a meeting, and a scale whose points cannot be
defended produces numbers that are argued about instead of acted on.

Both scores are **generated columns** in PostgreSQL rather than values the
application writes. A risk score that disagrees with its own factors is the
kind of thing nobody notices until an auditor recalculates one by hand.

| Band | Score | This project's label |
| --- | --- | --- |
| 15-25 | critical | |
| 10-14 | high | |
| 5-9 | medium | |
| 1-4 | low | |

The bands are this project's, for reporting. The numbers that *decide* anything
are the appetite and the escalation level below.

---

## 2. Inherent, residual, and the difference that has to be earned

**Inherent risk** is likelihood × impact before treatment.

**Residual risk** is the same product after treatment — and the tool takes a
narrow view of what "after treatment" means: the controls the risk names must
be **implemented**. Not planned. Not in progress.

A register where every risk is green because the treatment plan exists is the
most common form of comfortable fiction in a GRC tool. Here it produces a
finding (`RISK_TREATED_BY_UNIMPLEMENTED_CONTROL`), not a score, and the finding
says which controls are missing:

```
RSK-026 claims a residual score of 3 (from 10) on control(s) that are not
implemented: A.8.12
```

Two constraints back this up, in the database rather than in code:

- residual may not exceed inherent — a treatment that makes a risk worse is a
  typo, not a treatment;
- both residual factors, or neither — half a residual score is not a number
  anybody can act on.

---

## 3. Appetite and escalation

| Setting | Default | Meaning |
| --- | --- | --- |
| `SENTINEL_RISK_APPETITE` | 6 | at or below this, a residual risk needs no further treatment |
| `SENTINEL_RISK_ESCALATION` | 15 | at or above this, accepting a risk requires top management, not a line manager |

Both are configuration because both are business decisions. An organisation's
appetite for residual risk is argued about in a meeting and signed; it is not a
constant belonging inside a scoring function. Settings validation refuses an
escalation level at or below the appetite, because then every risk above
appetite is also an escalation and the distinction does nothing.

---

## 4. Acceptance covers a number, not a risk

This is the part that most registers get wrong.

An acceptance records **the score it was signed against**
(`residual_at_acceptance`). A risk accepted at 6 and later re-scored to 15 is
not an accepted risk; it carries a signature for a different number, and the
engine reports it as blocking.

Acceptances also:

- **expire.** An open-ended acceptance is a decision nobody revisits.
- **are singular.** A partial unique index allows exactly one acceptance in
  force per risk. Without it, "accepted" becomes a pile of overlapping
  approvals and nobody can say which one the auditor should read.
- **are kept when superseded.** Re-signing withdraws the previous acceptance
  and keeps the row, because "who accepted this, when, and who changed their
  mind" is a question that gets asked.
- **need a level.** A risk at or above the escalation level accepted by a Head
  of IT produces `HIGH_RISK_ACCEPTED_BELOW_MANAGEMENT`.

---

## 5. Treatment options

The four options in the standard's vocabulary, enforced by a `CHECK`:

| Option | What it means here |
| --- | --- |
| `modify` | apply controls; the risk must name at least one, or `RISK_MODIFIED_BY_NOTHING` fires |
| `retain` | accept it as it stands — which, above appetite, needs an acceptance on file |
| `avoid` | stop doing the thing |
| `share` | insurance, or a contract that moves the consequence |

---

## 6. What the register looks like on the demo data

28 risks over 22 assets, in two profiles:

| | mid-programme (`gaps`) | after remediation (`ready`) |
| --- | --- | --- |
| Risks | 28 | 28 |
| Carrying a residual score | 25 | 28 |
| Above appetite | 10 | 6 |
| With an acceptance on file | 3 | 6 |
| Inherent total | 335 | 335 |
| Effective total | 190 | 154 |
| Risk removed by treatment | 43.3% | 54.0% |

The inherent total does not move, and should not: treatment does not make a
threat go away, it makes the consequence or the likelihood smaller. A tool
whose "before" number improves is measuring its own optimism.

---

## 7. Limits

- **The method is asset-based.** ISO 27001 does not require that, and a
  scenario-based method would suit some organisations better. The engine would
  not care — a risk needs a threat, a vulnerability and two scores; the asset
  link is nullable.
- **Likelihood is judgement.** There is no frequency data behind these numbers,
  and pretending otherwise would be the dishonesty this project is written
  against. What the tool enforces is that the judgement is recorded, dated,
  owned and re-examined — not that it is right.
- **No aggregation across risks.** Ten medium risks on one asset may be worse
  than one high risk, and nothing here models that.
- **No cost of treatment.** A treatment plan that cannot be afforded is
  indistinguishable here from one that can.
