-- depends: 20260618000001_audit_2404_persistence

-- Durable backing for three meta verticals that were in-memory only:
-- experiment variants + assignments, A/B-test rollout records, and
-- pending HR pruning requests.

CREATE TABLE experiment_variants (
    experiment TEXT NOT NULL CHECK (LENGTH(TRIM(experiment)) > 0),
    variant TEXT NOT NULL CHECK (LENGTH(TRIM(variant)) > 0),
    weight INTEGER NOT NULL CHECK (weight >= 1 AND weight <= 1000),
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    PRIMARY KEY (experiment, variant)
);

-- ``list_for_experiment`` filters by experiment and orders by created_at.
CREATE INDEX idx_experiment_variants_exp_created
ON experiment_variants (experiment, created_at);

CREATE TABLE experiment_assignments (
    experiment TEXT NOT NULL CHECK (LENGTH(TRIM(experiment)) > 0),
    subject_id TEXT NOT NULL CHECK (LENGTH(TRIM(subject_id)) > 0),
    variant TEXT NOT NULL CHECK (LENGTH(TRIM(variant)) > 0),
    assigned_at TEXT NOT NULL CHECK (assigned_at LIKE '%+00:00' OR assigned_at LIKE '%Z'),
    PRIMARY KEY (experiment, subject_id),
    FOREIGN KEY (experiment, variant)
    REFERENCES experiment_variants (experiment, variant)
);

-- ``list_assignments`` pages newest-first within an experiment.
CREATE INDEX idx_experiment_assignments_exp_assigned
ON experiment_assignments (experiment, assigned_at DESC);

CREATE TABLE ab_tests (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    name TEXT NOT NULL CHECK (LENGTH(TRIM(name)) > 0),
    status TEXT NOT NULL CHECK (LENGTH(TRIM(status)) > 0),
    variants TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '%+00:00' OR updated_at LIKE '%Z')
);

-- ``list_items`` pages newest-first across all A/B tests.
CREATE INDEX idx_ab_tests_created ON ab_tests (created_at DESC);

-- One pending request per agent: the service keys ``_pending_requests``
-- by agent id and pops on completion / rejection, so agent_id is the
-- primary key. ``id`` (the request UUID) is retained as the audit ref.
CREATE TABLE pruning_requests (
    agent_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(agent_id)) > 0),
    id TEXT NOT NULL CHECK (LENGTH(TRIM(id)) > 0),
    agent_name TEXT NOT NULL CHECK (LENGTH(TRIM(agent_name)) > 0),
    evaluation TEXT NOT NULL,
    approval_id TEXT NOT NULL CHECK (LENGTH(TRIM(approval_id)) > 0),
    status TEXT NOT NULL CHECK (LENGTH(TRIM(status)) > 0),
    created_at TEXT NOT NULL CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    decided_at TEXT CHECK (decided_at IS NULL OR decided_at LIKE '%+00:00' OR decided_at LIKE '%Z'),
    decided_by TEXT
);

-- ``list_items`` pages pending requests oldest-first by created_at.
CREATE INDEX idx_pruning_requests_created ON pruning_requests (created_at);

-- Widen the connections.connection_type CHECK to the full ConnectionType enum.
-- The original constraint listed only 7 of 11 values, so persisting a
-- gitlab/gitea/forgejo connection -- or the llm_provider connection now minted
-- for every API-key provider credential -- failed the CHECK. SQLite cannot
-- ALTER a CHECK, so rebuild the table (yoyo applies migrations with
-- foreign_keys OFF, so the dependent FK rows are not cascade-deleted by the
-- drop; the rename restores the referenced name).
CREATE TABLE connections_new (
    name TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(name) > 0),
    connection_type TEXT NOT NULL CHECK (
        connection_type IN (
            'github', 'gitlab', 'gitea', 'forgejo', 'slack', 'smtp',
            'database', 'generic_http', 'oauth_app', 'a2a_peer', 'llm_provider'
        )
    ),
    auth_method TEXT NOT NULL CHECK (
        auth_method IN (
            'api_key', 'oauth2', 'basic_auth',
            'bearer_token', 'custom'
        )
    ),
    base_url TEXT,
    secret_refs_json TEXT NOT NULL DEFAULT '[]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0 CHECK (rate_limit_rpm >= 0),
    rate_limit_concurrent INTEGER NOT NULL DEFAULT 0
    CHECK (rate_limit_concurrent >= 0),
    health_check_enabled INTEGER NOT NULL DEFAULT 1
    CHECK (health_check_enabled IN (0, 1)),
    health_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (
        health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')
    ),
    last_health_check_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    webhook_receipt_retention_days INTEGER
    CHECK (
        webhook_receipt_retention_days IS NULL
        OR webhook_receipt_retention_days >= 0
    ),
    sensitive INTEGER NOT NULL DEFAULT 0 CHECK (sensitive IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO connections_new (
    name, connection_type, auth_method, base_url, secret_refs_json,
    rate_limit_rpm, rate_limit_concurrent, health_check_enabled,
    health_status, last_health_check_at, metadata_json,
    webhook_receipt_retention_days, sensitive, created_at, updated_at
)
SELECT
    name,
    connection_type,
    auth_method,
    base_url,
    secret_refs_json,
    rate_limit_rpm,
    rate_limit_concurrent,
    health_check_enabled,
    health_status,
    last_health_check_at,
    metadata_json,
    webhook_receipt_retention_days,
    sensitive,
    created_at,
    updated_at
FROM connections;

DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;

CREATE INDEX idx_connections_type ON connections (connection_type);
