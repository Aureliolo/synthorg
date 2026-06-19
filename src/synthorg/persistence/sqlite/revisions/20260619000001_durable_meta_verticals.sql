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

CREATE TABLE experiment_assignments (
    experiment TEXT NOT NULL CHECK (LENGTH(TRIM(experiment)) > 0),
    subject_id TEXT NOT NULL CHECK (LENGTH(TRIM(subject_id)) > 0),
    variant TEXT NOT NULL CHECK (LENGTH(TRIM(variant)) > 0),
    assigned_at TEXT NOT NULL CHECK (assigned_at LIKE '%+00:00' OR assigned_at LIKE '%Z'),
    PRIMARY KEY (experiment, subject_id)
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
