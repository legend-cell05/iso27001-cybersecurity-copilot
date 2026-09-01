-- ===========================================================================
-- Control state, clause state, evidence, and the management activities that
-- clause 9 requires.
--
-- The catalogue itself is not here. It is versioned YAML under `catalogue/`,
-- because what the controls *are* changes with a release of this software,
-- while what the organisation has *done about them* changes every week. Mixing
-- the two in one table is how a tool ends up unable to tell an operator's edit
-- from a standard's revision.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- What the organisation has decided and done about each Annex A control.
--
-- `applicable` plus `justification` is, between them, the Statement of
-- Applicability -- the document a certification audit opens first. The CHECK
-- is the whole point of storing it this way: excluding a control without
-- writing down why is the single most common reason an SoA is sent back.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.control_state (
    control_id      TEXT PRIMARY KEY,
    applicable      BOOLEAN NOT NULL DEFAULT TRUE,
    justification   TEXT,
    status          TEXT NOT NULL DEFAULT 'not_started',
    owner_role      TEXT,
    maturity        SMALLINT NOT NULL DEFAULT 0,
    implemented_on  DATE,
    last_reviewed   DATE,
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_control_status CHECK (
        status IN ('not_started', 'planned', 'in_progress', 'implemented', 'excluded')
    ),
    -- 0 nothing, 1 ad hoc, 2 repeatable, 3 defined, 4 measured, 5 optimising.
    CONSTRAINT ck_control_maturity CHECK (maturity BETWEEN 0 AND 5),
    -- An exclusion without a reason is the finding auditors raise most often.
    CONSTRAINT ck_control_exclusion_justified CHECK (
        applicable OR (justification IS NOT NULL AND length(trim(justification)) > 0)
    ),
    -- An excluded control cannot also be implemented, and an applicable one
    -- cannot be 'excluded'. The two fields have to agree.
    CONSTRAINT ck_control_status_agrees CHECK (
        (applicable AND status <> 'excluded') OR (NOT applicable AND status = 'excluded')
    ),
    -- "Implemented" is a claim with a date on it.
    CONSTRAINT ck_control_implemented_dated CHECK (
        status <> 'implemented' OR implemented_on IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS ix_control_status ON ${SCHEMA}.control_state (status);
CREATE INDEX IF NOT EXISTS ix_control_owner  ON ${SCHEMA}.control_state (owner_role);

-- ---------------------------------------------------------------------------
-- The management-system clauses (4 to 10).
--
-- Annex A gets the attention, but a certificate is granted against the
-- clauses: scope, leadership, planning, competence, internal audit,
-- management review, improvement. An organisation with 93 implemented
-- controls and no management review does not get certified, and a tool that
-- only tracks Annex A will tell it everything is fine.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.requirement_state (
    requirement_id  TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'not_started',
    owner_role      TEXT,
    completed_on    DATE,
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_requirement_status CHECK (
        status IN ('not_started', 'in_progress', 'met')
    ),
    CONSTRAINT ck_requirement_met_dated CHECK (
        status <> 'met' OR completed_on IS NOT NULL
    )
);

-- ---------------------------------------------------------------------------
-- Evidence.
--
-- An implemented control with no evidence is an assertion. This table is the
-- index of what can be shown to an auditor, and three columns carry the
-- weight:
--
--   `content_hash` -- the SHA-256 of the artefact as collected. It is what
--   makes "this is the policy that was in force in March" a checkable claim
--   rather than a memory.
--
--   `collected_on`  -- evidence ages. A penetration test from 2023 does not
--   support a control today, and the freshness rule that decides this is per
--   control, from the catalogue.
--
--   `valid_days`    -- an override, for the cases where the organisation's own
--   policy is stricter than the catalogue default.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.evidence (
    evidence_id     BIGSERIAL PRIMARY KEY,
    -- Either an Annex A control or a management-system clause requirement.
    -- Both need evidence; an auditor asking to see the documented scope is
    -- asking for evidence of clause 4.3 exactly as much as a screenshot of a
    -- firewall rule is evidence of A.8.20.
    subject_kind    TEXT NOT NULL DEFAULT 'control',
    subject_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    collected_on    DATE NOT NULL,
    collected_by    TEXT NOT NULL,
    location        TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    valid_days      INT,
    superseded_on   DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_evidence_subject CHECK (subject_kind IN ('control', 'requirement')),
    CONSTRAINT ck_evidence_kind CHECK (
        kind IN ('policy', 'procedure', 'record', 'log', 'report',
                 'screenshot', 'ticket', 'certificate', 'contract')
    ),
    -- A SHA-256 hex digest, or nothing. A "hash" that is not one is worse
    -- than no hash, because it looks like proof.
    CONSTRAINT ck_evidence_hash CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_evidence_valid_days CHECK (valid_days IS NULL OR valid_days > 0)
);

CREATE INDEX IF NOT EXISTS ix_evidence_subject ON ${SCHEMA}.evidence (subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS ix_evidence_current ON ${SCHEMA}.evidence (subject_id, collected_on DESC)
    WHERE superseded_on IS NULL;

-- ---------------------------------------------------------------------------
-- Internal audits, management reviews and risk reviews.
--
-- Clause 9 requires the first two to happen, on a planned interval, before
-- certification. `covers` records what an internal audit actually looked at,
-- because "we did an internal audit" and "we audited the four controls that
-- were easiest to evidence" are different statements.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.management_activity (
    activity_id     BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,
    performed_on    DATE NOT NULL,
    performed_by    TEXT NOT NULL,
    summary         TEXT NOT NULL,
    covers          JSONB NOT NULL DEFAULT '[]'::JSONB,
    findings_raised INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_activity_kind CHECK (
        kind IN ('internal_audit', 'management_review', 'risk_review',
                 'awareness_training', 'incident_exercise')
    ),
    CONSTRAINT ck_activity_findings CHECK (findings_raised >= 0)
);

CREATE INDEX IF NOT EXISTS ix_activity_kind ON ${SCHEMA}.management_activity (kind, performed_on DESC);
