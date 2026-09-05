# Operations

> Synthetic data. Nothing here has been deployed; this is how the tool is meant
> to be run, written so that the claim can be checked against the code.

---

## 1. Installation

```bash
git clone https://github.com/legend-cell05/iso27001-cybersecurity-copilot.git
cd iso27001-cybersecurity-copilot

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # then edit the database settings
sentinel doctor               # configuration, database, catalogue, registers
sentinel init-db
```

`sentinel doctor` is the first thing to run and the first thing to run again
when something is wrong. It distinguishes a database that is unreachable from
one that is reachable but not initialised, and it prints the catalogue
fingerprint.

---

## 2. A first pass through the tool

```bash
sentinel demo --profile gaps   # a simulated organisation with planted defects
sentinel assess                # every rule and the gate
sentinel findings              # all of them, with remediations
sentinel readiness             # the verdict; exit code 3 when refused
sentinel export                # the documents
```

---

## 3. The commands

### Inspection

| Command | Does |
| --- | --- |
| `sentinel doctor` | configuration, connectivity, catalogue, register counts |
| `sentinel catalogue [--theme X] [--capability Y]` | the control catalogue |
| `sentinel controls [--status X] [--theme Y] [--outstanding]` | applicability decisions and their state |
| `sentinel risks [--above-appetite]` | the register, worst first |
| `sentinel evidence [--stale]` | coverage and freshness per control |
| `sentinel posture` | coverage by capability, and clause 9 |
| `sentinel history` | past assessments — the trend is the point |

### Assessment

| Command | Does | Exit codes |
| --- | --- | --- |
| `sentinel assess` | run every rule and the gate, keep the result | 0, 1 |
| `sentinel findings [--severity] [--rule]` | the last assessment's findings | 0, 1 |
| `sentinel readiness [--force --reason]` | the gate's verdict | 0, **3** |
| `sentinel export [--kind]` | write the documents | 0, 1 |
| `sentinel cycle [--profile]` | seed, assess, export, gate | 0, **3** |

### Changing the registers

| Command | Does |
| --- | --- |
| `sentinel accept-risk RSK-016 --by "H. Rousseau" --role "Managing Director (top management)" --rationale "…"` | record a signed acceptance |
| `sentinel record-activity internal_audit --by "…" --summary "…" [--on YYYY-MM-DD]` | record an audit, review or risk review |
| `sentinel demo --profile gaps\|ready` | replace the registers with the simulated organisation |
| `sentinel reset [--yes]` | drop the schema and everything in it |

### Serving

```bash
sentinel serve --host 0.0.0.0 --port 8000     # OpenAPI at /docs
```

| Endpoint | Does |
| --- | --- |
| `GET /health` | liveness, database, whether the schema exists |
| `GET /api/v1/catalogue` | version, fingerprint, counts, the copyright position |
| `GET /api/v1/posture` | coverage, risk position, finding counts |
| `GET /api/v1/findings` | the last stored assessment's findings |
| `GET /api/v1/readiness` | the gate — **200** ready, **409** refused |
| `GET /api/v1/risks` | the register, with acceptance status |
| `GET /api/v1/history` | past assessments |

---

## 4. A realistic cadence

| When | What | Why |
| --- | --- | --- |
| Nightly, in CI | `sentinel readiness` | exit code 3 surfaces a regression the day it appears, not at the audit |
| Weekly | `sentinel evidence --stale` | evidence decays silently; this is the only way it gets noticed early |
| Monthly | `sentinel assess`, `sentinel export` | the assessment history is itself evidence of clause 9.1 monitoring |
| Quarterly | re-score the register, `sentinel record-activity risk_review` | and the acceptances that no longer cover their risks surface immediately |
| Annually | internal audit, management review | clause 9.2 and 9.3, both of which the gate requires |

---

## 5. Docker

```bash
make up          # PostgreSQL, one assessment run, then the API
make down
make logs
docker compose run --rm assess readiness
```

The `assess` service runs `cycle` and exits with the gate's code — 3 for the
default `gaps` profile, because that ISMS is not ready. The `api` service
depends on `service_started` rather than `service_completed_successfully` for
exactly that reason: the API is most useful when the answer is "not ready".

The image runs as a non-root user (UID 10001) and carries the catalogue in a
root-owned directory, so the process that reads the control set cannot edit it.
CI asserts both.

> The images have never been built in the environment this project was
> developed in — no Docker daemon was available. CI builds and smoke-tests them
> on every push, which is the only claim being made about them.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `database unreachable` | wrong host/port, or PostgreSQL is not running | check `.env`, `sentinel doctor` |
| `the ISMS schema does not exist yet` | reachable but not initialised | `sentinel init-db` |
| `no control 'A.5.99' in catalogue 1.0.0` | a register references an identifier the catalogue does not have | fix the register, or load the catalogue version that has it |
| `unknown capability 'vibes'` at load | a catalogue file uses a value the manifest does not define | add it to `catalogue.yaml`, or fix the typo |
| `readiness` exits 3 every night | the ISMS is not ready | read the failing checks; that is the tool working |
| An acceptance "stale after rescoring" | the risk was re-scored above the signed number | re-obtain the acceptance, or treat the risk back down |

---

## 7. Backup and retention

The schema holds every assessment, every finding and every evidence reference.
It is the record that the organisation ran a management system, so:

- back it up with the rest of the database, and test the restore — the tool
  will otherwise report `A.8.13` as implemented while its own backup is not;
- do not truncate `assessment` to "clean up". The sequence is the evidence, and
  an ISMS whose history starts last month looks exactly like one that started
  last month;
- there is **no retention policy implemented**, and a real deployment needs
  one, because `finding` and `evidence` name people and systems.
