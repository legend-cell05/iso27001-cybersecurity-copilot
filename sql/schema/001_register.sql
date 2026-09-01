-- ===========================================================================
-- The schema, the assets and the risk register.
--
-- Everything here is `IF NOT EXISTS`. Initialisation runs on every start-up,
-- and a compliance tool that empties its own evidence index when it starts is
-- a tool that destroys the only record an organisation has that it ever did
-- the work. A regression test asserts that re-running this file changes
-- nothing.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS ${SCHEMA};

-- ---------------------------------------------------------------------------
-- Assets.
--
-- ISO 27001 does not mandate an asset-based risk method, but the organisation
-- has to know what it is protecting, and a risk with no subject is a sentence
-- rather than a risk. `owner` is a role, not a person: people leave, and an
-- asset whose owner left has no owner, which is exactly the finding an
-- auditor raises.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.asset (
    asset_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    owner_role      TEXT NOT NULL,
    classification  TEXT NOT NULL,
    criticality     SMALLINT NOT NULL,
    site            TEXT,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_asset_kind CHECK (
        kind IN ('information', 'software', 'hardware', 'service', 'people', 'site')
    ),
    CONSTRAINT ck_asset_classification CHECK (
        classification IN ('public', 'internal', 'confidential', 'restricted')
    ),
    CONSTRAINT ck_asset_criticality CHECK (criticality BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS ix_asset_owner ON ${SCHEMA}.asset (owner_role);

-- ---------------------------------------------------------------------------
-- Risks.
--
-- Likelihood and impact are 1-5, so the score is 1-25. Both scores are
-- GENERATED columns rather than values the application writes: a risk score
-- that disagrees with its own factors is the kind of thing nobody notices
-- until an auditor recalculates one by hand.
--
-- The residual scores are nullable until the risk has been treated. The CHECK
-- is the interesting one: residual risk may not exceed inherent risk. A
-- treatment that makes a risk worse is not a treatment, it is a typo, and
-- letting the database refuse it costs nothing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.risk (
    risk_id             TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT,
    asset_id            TEXT REFERENCES ${SCHEMA}.asset (asset_id) ON DELETE SET NULL,
    threat              TEXT NOT NULL,
    vulnerability       TEXT NOT NULL,
    owner_role          TEXT NOT NULL,

    likelihood          SMALLINT NOT NULL,
    impact              SMALLINT NOT NULL,
    inherent_score      SMALLINT GENERATED ALWAYS AS (likelihood * impact) STORED,

    treatment           TEXT NOT NULL DEFAULT 'modify',
    residual_likelihood SMALLINT,
    residual_impact     SMALLINT,
    residual_score      SMALLINT GENERATED ALWAYS AS (
                            residual_likelihood * residual_impact
                        ) STORED,

    status              TEXT NOT NULL DEFAULT 'open',
    identified_on       DATE NOT NULL DEFAULT CURRENT_DATE,
    review_due          DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_risk_scales CHECK (
        likelihood BETWEEN 1 AND 5
        AND impact BETWEEN 1 AND 5
        AND (residual_likelihood IS NULL OR residual_likelihood BETWEEN 1 AND 5)
        AND (residual_impact IS NULL OR residual_impact BETWEEN 1 AND 5)
    ),
    -- A treatment cannot raise the risk it treats.
    CONSTRAINT ck_risk_residual_not_worse CHECK (
        residual_likelihood IS NULL
        OR residual_impact IS NULL
        OR (residual_likelihood * residual_impact) <= (likelihood * impact)
    ),
    -- The four options in the standard's vocabulary, and nothing else.
    CONSTRAINT ck_risk_treatment CHECK (
        treatment IN ('modify', 'retain', 'avoid', 'share')
    ),
    CONSTRAINT ck_risk_status CHECK (
        status IN ('open', 'treated', 'accepted', 'closed')
    ),
    -- Both residual factors, or neither. Half a residual score is not a
    -- number anybody can act on.
    CONSTRAINT ck_risk_residual_pair CHECK (
        (residual_likelihood IS NULL) = (residual_impact IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_risk_asset  ON ${SCHEMA}.risk (asset_id);
CREATE INDEX IF NOT EXISTS ix_risk_status ON ${SCHEMA}.risk (status);
CREATE INDEX IF NOT EXISTS ix_risk_owner  ON ${SCHEMA}.risk (owner_role);

-- ---------------------------------------------------------------------------
-- Which controls treat which risks.
--
-- This link table is what makes a Statement of Applicability defensible: a
-- control is applicable *because* it treats a named risk, and the
-- justification for including it writes itself. It is also what lets the
-- engine find the opposite case -- a risk whose treatment relies on a control
-- nobody has implemented.
--
-- `control_id` is not a foreign key. The catalogue lives in version-controlled
-- YAML rather than in a table, because it is code-adjacent reference data that
-- changes with a release and not with an operator's afternoon. The loader
-- checks every referenced identifier against the catalogue at assessment time,
-- and reports the ones that no longer exist.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.risk_control (
    risk_id     TEXT NOT NULL REFERENCES ${SCHEMA}.risk (risk_id) ON DELETE CASCADE,
    control_id  TEXT NOT NULL,
    rationale   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (risk_id, control_id)
);

CREATE INDEX IF NOT EXISTS ix_risk_control_control ON ${SCHEMA}.risk_control (control_id);

-- ---------------------------------------------------------------------------
-- Risk acceptances.
--
-- Accepting a risk is a decision by a named person with a role, not a state a
-- row drifts into. The partial unique index enforces the property that makes
-- the record meaningful: a risk has at most one acceptance in force at a
-- time. Without it, "accepted" becomes a pile of overlapping approvals and
-- nobody can say which one an auditor should be reading.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ${SCHEMA}.risk_acceptance (
    acceptance_id   BIGSERIAL PRIMARY KEY,
    risk_id         TEXT NOT NULL REFERENCES ${SCHEMA}.risk (risk_id) ON DELETE CASCADE,
    accepted_by     TEXT NOT NULL,
    accepted_role   TEXT NOT NULL,
    accepted_on     DATE NOT NULL DEFAULT CURRENT_DATE,
    expires_on      DATE,
    rationale       TEXT NOT NULL,
    -- The score at the moment of acceptance. If the risk is re-scored later,
    -- the acceptance no longer covers what it was signed for, and the engine
    -- says so.
    residual_at_acceptance SMALLINT NOT NULL,
    withdrawn_on    DATE,

    CONSTRAINT ck_acceptance_rationale CHECK (length(trim(rationale)) > 0),
    CONSTRAINT ck_acceptance_dates CHECK (
        expires_on IS NULL OR expires_on > accepted_on
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_acceptance_one_in_force
    ON ${SCHEMA}.risk_acceptance (risk_id)
    WHERE withdrawn_on IS NULL;

CREATE INDEX IF NOT EXISTS ix_acceptance_risk ON ${SCHEMA}.risk_acceptance (risk_id);
