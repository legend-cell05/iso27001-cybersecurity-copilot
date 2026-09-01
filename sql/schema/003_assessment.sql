-- ===========================================================================
-- Assessments, findings and readiness checks.
--
-- An assessment is a dated, reproducible statement about the ISMS: these
-- controls were applicable, this much of the register was treated, these
-- findings stood, and the gate said this. It is kept rather than overwritten
-- because the sequence is the evidence: an auditor asking "how did you know
-- you were ready?" is asking for this table.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ${SCHEMA}.assessment (
    assessment_id       BIGSERIAL PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    organisation        TEXT NOT NULL,
    scope               TEXT NOT NULL,

    -- Which catalogue produced this. The version is what a human quotes; the
    -- fingerprint is what a machine compares, because two catalogues can
    -- carry the same version number and different content, and only one of
    -- them was the one that ran.
    catalogue_version   TEXT NOT NULL,
    catalogue_fingerprint TEXT NOT NULL,

    -- The thresholds in force at the time, frozen. A gate result means
    -- nothing without them: "ready" under a 50% implementation bar is not
    -- the same claim as "ready" under 95%.
    parameters          JSONB NOT NULL DEFAULT '{}'::JSONB,
    totals              JSONB NOT NULL DEFAULT '{}'::JSONB,

    ready               BOOLEAN NOT NULL DEFAULT FALSE,
    blocking_findings   INT NOT NULL DEFAULT 0,
    forced              BOOLEAN NOT NULL DEFAULT FALSE,
    forced_reason       TEXT,

    CONSTRAINT ck_assessment_fingerprint CHECK (catalogue_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_assessment_blocking CHECK (blocking_findings >= 0),
    -- Declaring readiness over the gate's objection is allowed and recorded.
    -- Doing it without saying why is not.
    CONSTRAINT ck_assessment_forced_reason CHECK (
        NOT forced OR (forced_reason IS NOT NULL AND length(trim(forced_reason)) > 0)
    )
);

CREATE INDEX IF NOT EXISTS ix_assessment_run_at ON ${SCHEMA}.assessment (run_at DESC);

-- ---------------------------------------------------------------------------
-- Findings.
--
-- Every finding names the rule that produced it. That is what makes the
-- output arguable: "47 issues" is a number, "47 issues, of which 12 are
-- CONTROL_WITHOUT_EVIDENCE" is a plan, and a rule an organisation disagrees
-- with can be discussed rather than ignored wholesale.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.finding (
    finding_id      BIGSERIAL PRIMARY KEY,
    assessment_id   BIGINT NOT NULL
                    REFERENCES ${SCHEMA}.assessment (assessment_id) ON DELETE CASCADE,
    rule            TEXT NOT NULL,
    severity        TEXT NOT NULL,
    subject_kind    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    detail          TEXT NOT NULL,
    remediation     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_finding_severity CHECK (
        severity IN ('blocking', 'high', 'medium', 'low', 'info')
    ),
    CONSTRAINT ck_finding_subject CHECK (
        subject_kind IN ('control', 'requirement', 'risk', 'evidence', 'asset', 'programme')
    ),
    -- A finding that does not say what to do about it is a complaint.
    CONSTRAINT ck_finding_remediation CHECK (length(trim(remediation)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_finding_assessment ON ${SCHEMA}.finding (assessment_id);
CREATE INDEX IF NOT EXISTS ix_finding_rule       ON ${SCHEMA}.finding (rule);
CREATE INDEX IF NOT EXISTS ix_finding_blocking   ON ${SCHEMA}.finding (assessment_id)
    WHERE severity = 'blocking';

-- ---------------------------------------------------------------------------
-- The readiness gate's own checks.
--
-- Stored separately from the findings because they answer a different
-- question. A finding says "this control has no evidence". A check says
-- "94.6% of applicable controls are implemented, against a bar of 95%" --
-- with the number and the threshold that produced the verdict, so that a
-- refusal can be argued with rather than merely obeyed.
--
-- A skipped check is recorded as skipped, never as a pass. A gate that
-- reports six passes when it ran four is a gate nobody should trust.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.readiness_check (
    check_id        BIGSERIAL PRIMARY KEY,
    assessment_id   BIGINT NOT NULL
                    REFERENCES ${SCHEMA}.assessment (assessment_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    passed          BOOLEAN NOT NULL,
    skipped         BOOLEAN NOT NULL DEFAULT FALSE,
    detail          TEXT NOT NULL,
    value           NUMERIC(10, 4),
    threshold       NUMERIC(10, 4),

    CONSTRAINT ck_check_skipped_not_failed CHECK (NOT skipped OR passed)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_check_per_assessment
    ON ${SCHEMA}.readiness_check (assessment_id, name);

-- ---------------------------------------------------------------------------
-- Exports.
--
-- Which document was produced from which assessment, and its hash. An SoA
-- emailed to an auditor months ago can then be matched back to the state of
-- the ISMS that produced it -- including when that state has since moved on.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.export (
    export_id       BIGSERIAL PRIMARY KEY,
    assessment_id   BIGINT NOT NULL
                    REFERENCES ${SCHEMA}.assessment (assessment_id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    path            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_export_kind CHECK (
        kind IN ('soa', 'risk_treatment_plan', 'findings', 'audit_dossier', 'posture')
    ),
    CONSTRAINT ck_export_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS ix_export_assessment ON ${SCHEMA}.export (assessment_id);
