# Security

> Synthetic data throughout. No real organisation, asset, risk or evidence
> artefact exists in this repository, and nothing here has been deployed.

A tool that tells an organisation whether its security management is in order
had better be able to say the same about itself. So: the controls that **are**
implemented and verifiable by reading the code, then the ones that would be
required before this ran anywhere real.

There is a particular risk profile here worth naming first. This system holds a
map of an organisation's weaknesses — which controls are missing, which risks
are unaccepted, where the evidence has lapsed. A copy of the `isms` schema is a
target package for anyone attacking that organisation. It should be treated as
being as sensitive as the systems it describes.

---

## 1. Secrets

**No credential is in the repository.**

| Control | Where |
| --- | --- |
| `.env` is git-ignored; `.env.example` holds placeholders only | `.gitignore`, `.env.example` |
| The ignore rule is `.env` **and** `.env.*`, with a `!.env.example` exception | `.gitignore` |
| The database password is a Pydantic `SecretStr` | `config.py` |
| Logs, `doctor` and every error use `safe_dsn`, never `dsn` | `config.py`, `db/engine.py`, `cli.py` |
| Compose defaults are obviously-local placeholders | `docker-compose.yml` |
| CI's PostgreSQL password is a test-only literal on an ephemeral container | `.github/workflows/ci.yml` |

`SecretStr` is the control that does real work. A plain `str` leaks through any
`repr()` of the settings object — which is what an unhandled exception prints
in a traceback, and what a logging framework prints when somebody logs the
config to debug a connection problem. With `SecretStr` the same traceback shows
`SecretStr('**********')`.

---

## 2. SQL

Every value is a bound parameter. Exactly one thing is interpolated: the schema
name, which cannot be bound.

It comes from configuration and is validated in `config.py` against
`^[a-z_][a-z0-9_]*$` before it reaches a statement. The allow-list is the
control — a schema name that would change the meaning of a statement cannot
pass the validator. The DDL lives in `.sql` files rather than Python strings so
that a reviewer can read what runs without reading the code that runs it.

---

## 3. The container

| Control | Detail |
| --- | --- |
| Multi-stage build | the runtime image carries no compiler and no build cache |
| Non-root | a dedicated user at UID 10001, created in the image |
| **Read-only catalogue** | `catalogue/` and `sql/` are copied root-owned; the application user can read them and cannot edit them |
| No secrets baked in | configuration arrives as environment at run time |
| Healthcheck | `sentinel doctor` |
| `.dockerignore` | excludes `.env`, `data/`, tests, docs, the git directory |

CI asserts both properties rather than trusting them:

```bash
test "$(docker run --rm --entrypoint id …:ci -u)" != "0"
! docker run --rm --entrypoint sh …:ci -c 'echo x >> /app/catalogue/catalogue.yaml'
```

The second one is the interesting assertion. A Statement of Applicability whose
control set can be edited at run time is a Statement of Applicability that can
be changed without a deployment, and therefore without a review.

> The images have never been built in the environment this project was
> developed in — no Docker daemon was available there. CI builds them and
> smoke-tests them on every push, which is the only claim being made about them.

---

## 4. Integrity of the record

Four mechanisms, because the value of this system is that its record can be
trusted:

**The catalogue fingerprint.** A SHA-256 over every catalogue file, recorded on
every assessment. A document produced in March can be matched to the control
set that produced it, and a catalogue edited without a version bump is still
distinguishable.

**Evidence hashes.** Every evidence record carries the SHA-256 of the artefact
as collected, with a `CHECK` constraint that it is a real hex digest. A "hash"
that is not one looks like proof and is not.

**Export hashes.** Every generated document is hashed and recorded against its
assessment, so a document edited after the fact no longer matches its own
record.

**Frozen parameters.** Each assessment stores the thresholds it ran under.
"Ready" under a 50% implementation bar is not the same claim as "ready" under
95%, and six months later nobody remembers which was in force.

None of these is a signature. They detect drift and mistakes; they do not stop
somebody with database access from rewriting the row and the hash together.
Signing would need a key and a trust anchor, and is not implemented.

---

## 5. What is not implemented

Named, because pretending otherwise is the failure this document exists to
avoid:

- **No authentication on the API.** It binds to localhost by default and there
  is no token, no mTLS, no rate limiting. It is read-only, which limits the
  damage to disclosure — but what it discloses is a map of the organisation's
  weaknesses, so it must not be exposed.
- **No identities, and therefore no audit log of who changed what.** The
  registers record *roles* (an acceptance names a person as text). There is no
  authenticated user behind a change, so `--force` records a reason and not a
  who.
- **No secret manager.** Configuration comes from `.env`. A real deployment
  means Azure Key Vault or equivalent, with rotation.
- **No row-level or column-level access control.** One database role reads and
  writes everything.
- **No retention or deletion path** for findings, evidence references or
  assessments — all of which name people and systems.
- **No signing of assessments or exports** (above).
- **No multi-tenancy.** One organisation per schema, and nothing prevents a
  misconfigured `SENTINEL_SCHEMA` from pointing at another one.
- **No dependency signing or SBOM.** Dependencies are pinned by range and
  installed from PyPI.

---

## 6. Data provenance

Every number in this repository comes from `sentinel demo`, a seeded generator
in `demo.py`. The organisation is called Meridian Logistics SAS; the assets are
`AST-001` upward; the evidence hashes are derived from the artefacts' own
labels precisely so that they are verifiable as synthetic.

It is not anonymised real data, not scraped, and not derived from any dataset
with a licence to respect. The README, every document in `docs/` and every
generated export say so at the top, because a compliance project that is
ambiguous about where its data came from has failed at the first thing it
claims to care about.
