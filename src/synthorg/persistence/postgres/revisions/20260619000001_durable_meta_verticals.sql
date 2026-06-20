-- depends: 20260618000001_audit_2404_persistence

-- Durable backing for three meta verticals that were in-memory only:
-- experiment variants + assignments, A/B-test rollout records, and
-- pending HR pruning requests.

CREATE TABLE experiment_variants (
    experiment TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(experiment)) > 0),
    variant TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(variant)) > 0),
    weight INTEGER NOT NULL CHECK (weight >= 1 AND weight <= 1000),
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (experiment, variant)
);

-- ``list_for_experiment`` filters by experiment and orders by created_at.
CREATE INDEX idx_experiment_variants_exp_created
ON experiment_variants (experiment, created_at);

CREATE TABLE experiment_assignments (
    experiment TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(experiment)) > 0),
    subject_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(subject_id)) > 0),
    variant TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(variant)) > 0),
    assigned_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (experiment, subject_id),
    FOREIGN KEY (experiment, variant)
    REFERENCES experiment_variants (experiment, variant)
);

-- ``list_assignments`` pages newest-first within an experiment.
CREATE INDEX idx_experiment_assignments_exp_assigned
ON experiment_assignments (experiment, assigned_at DESC);

CREATE TABLE ab_tests (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    status TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(status)) > 0),
    variants JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- ``list_items`` pages newest-first across all A/B tests.
CREATE INDEX idx_ab_tests_created ON ab_tests (created_at DESC);

-- One pending request per agent: the service keys ``_pending_requests``
-- by agent id and pops on completion / rejection, so agent_id is the
-- primary key. ``id`` (the request UUID) is retained as the audit ref.
CREATE TABLE pruning_requests (
    agent_id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(agent_id)) > 0),
    id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    agent_name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(agent_name)) > 0),
    evaluation JSONB NOT NULL,
    approval_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(approval_id)) > 0),
    status TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(status)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    decided_by TEXT
);

-- ``list_items`` pages pending requests oldest-first by created_at.
CREATE INDEX idx_pruning_requests_created ON pruning_requests (created_at);

-- Widen the connections.connection_type CHECK to the full ConnectionType enum.
-- The original constraint listed only 7 of 11 values, so persisting a
-- gitlab/gitea/forgejo connection -- or the llm_provider connection now minted
-- for every API-key provider credential -- failed the CHECK. The inline column
-- CHECK is auto-named ``connections_connection_type_check``.
ALTER TABLE connections
DROP CONSTRAINT connections_connection_type_check;

ALTER TABLE connections
ADD CONSTRAINT connections_connection_type_check CHECK (
    connection_type IN (
        'github', 'gitlab', 'gitea', 'forgejo', 'slack', 'smtp',
        'database', 'generic_http', 'oauth_app', 'a2a_peer', 'llm_provider'
    )
);
