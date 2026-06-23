-- Bring SQLite TEXT columns up to the same integrity guarantees the
-- Postgres sibling enforces natively. SQLite keeps the sanctioned TEXT
-- encoding for JSON (json.dumps) and timestamps (ISO-8601), but several
-- columns lacked the canonical CHECK guards the rest of the schema uses,
-- so a malformed JSON blob or a non-UTC timestamp could land silently
-- where the Postgres JSONB / TIMESTAMPTZ column would have rejected it.
--
-- SQLite cannot ALTER an existing column to add a CHECK constraint, so
-- each affected table is rebuilt (create-new, copy, drop, rename) and its
-- indices recreated. There are no inbound foreign keys to these tables,
-- so the rebuild needs no FK toggling; project_brain_entries keeps its
-- outbound FK to projects.

CREATE TABLE project_charters_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL CHECK (LENGTH(TRIM(conversation_id)) > 0),
    created_by TEXT NOT NULL CHECK (LENGTH(TRIM(created_by)) > 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status TEXT NOT NULL DEFAULT 'drafted' CHECK (
        status IN ('drafted', 'approved', 'cancelled')
    ),
    title TEXT NOT NULL CHECK (LENGTH(TRIM(title)) > 0),
    brief TEXT NOT NULL CHECK (LENGTH(TRIM(brief)) > 0),
    goals TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(goals) AND JSON_TYPE(goals) = 'array'),
    constraints TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(constraints) AND JSON_TYPE(constraints) = 'array'),
    success_criteria TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(success_criteria) AND JSON_TYPE(success_criteria) = 'array'),
    in_scope TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(in_scope) AND JSON_TYPE(in_scope) = 'array'),
    out_of_scope TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(out_of_scope) AND JSON_TYPE(out_of_scope) = 'array'),
    envelope_amount REAL NOT NULL CHECK (envelope_amount > 0),
    envelope_currency TEXT NOT NULL CHECK (LENGTH(envelope_currency) = 3),
    envelope_deadline TEXT
    CHECK (
        envelope_deadline IS NULL
        OR envelope_deadline LIKE '%+00:00'
        OR envelope_deadline LIKE '%Z'
    ),
    envelope_time_horizon TEXT
    CHECK (
        envelope_time_horizon IS NULL
        OR LENGTH(TRIM(envelope_time_horizon)) > 0
    ),
    project_id TEXT CHECK (project_id IS NULL OR LENGTH(TRIM(project_id)) > 0),
    proposed_project_name TEXT
    CHECK (
        proposed_project_name IS NULL
        OR LENGTH(TRIM(proposed_project_name)) > 0
    ),
    proposed_project_description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    updated_at TEXT NOT NULL CHECK (
        updated_at LIKE '%+00:00' OR updated_at LIKE '%Z'
    ),
    approved_at TEXT
    CHECK (
        approved_at IS NULL
        OR approved_at LIKE '%+00:00'
        OR approved_at LIKE '%Z'
    ),
    approved_by TEXT
    CHECK (approved_by IS NULL OR LENGTH(TRIM(approved_by)) > 0),
    forecast_id TEXT
    CHECK (forecast_id IS NULL OR LENGTH(TRIM(forecast_id)) > 0),
    correlation_id TEXT
    CHECK (correlation_id IS NULL OR LENGTH(TRIM(correlation_id)) > 0),
    task_id TEXT CHECK (task_id IS NULL OR LENGTH(TRIM(task_id)) > 0),
    CONSTRAINT chk_charter_project_binding CHECK (
        (project_id IS NOT NULL AND proposed_project_name IS NULL)
        OR (project_id IS NULL AND proposed_project_name IS NOT NULL)
    ),
    CONSTRAINT chk_charter_approval_coupling CHECK (
        (
            status = 'approved'
            AND approved_at IS NOT NULL AND approved_by IS NOT NULL
            AND forecast_id IS NOT NULL AND correlation_id IS NOT NULL
            AND task_id IS NOT NULL
        )
        OR (
            status != 'approved'
            AND approved_at IS NULL AND approved_by IS NULL
            AND forecast_id IS NULL AND correlation_id IS NULL
            AND task_id IS NULL
        )
    )
);

INSERT INTO project_charters_new (
    id, conversation_id, created_by, version, status, title, brief, goals,
    constraints, success_criteria, in_scope, out_of_scope, envelope_amount,
    envelope_currency, envelope_deadline, envelope_time_horizon, project_id,
    proposed_project_name, proposed_project_description, created_at, updated_at,
    approved_at, approved_by, forecast_id, correlation_id, task_id
)
SELECT
    id, conversation_id, created_by, version, status, title, brief, goals,
    constraints, success_criteria, in_scope, out_of_scope, envelope_amount,
    envelope_currency, envelope_deadline, envelope_time_horizon, project_id,
    proposed_project_name, proposed_project_description, created_at, updated_at,
    approved_at, approved_by, forecast_id, correlation_id, task_id
FROM project_charters;
DROP TABLE project_charters;
ALTER TABLE project_charters_new RENAME TO project_charters;

CREATE INDEX idx_project_charters_status ON project_charters (status);
CREATE INDEX idx_project_charters_project_id ON project_charters (project_id);
CREATE INDEX idx_project_charters_created_by ON project_charters (created_by);
CREATE INDEX idx_project_charters_conversation_id
ON project_charters (conversation_id);
CREATE INDEX idx_project_charters_created_id
ON project_charters (created_at DESC, id DESC);

CREATE TABLE project_brain_entries_new (
    project_id TEXT NOT NULL
    CHECK (LENGTH(TRIM(project_id)) > 0),
    entry_id TEXT NOT NULL
    CHECK (LENGTH(TRIM(entry_id)) > 0),
    revision INTEGER NOT NULL
    CHECK (revision >= 1),
    entry_kind TEXT NOT NULL
    CHECK (entry_kind IN (
        'decision', 'open_question', 'blocker', 'risk',
        'dependency', 'plan_revision'
    )),
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL,
    author TEXT NOT NULL,
    recorded_at TEXT NOT NULL
    CHECK (recorded_at LIKE '%+00:00' OR recorded_at LIKE '%Z'),
    related_task_ids TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(related_task_ids) AND JSON_TYPE(related_task_ids) = 'array'),
    related_entry_ids TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(related_entry_ids) AND JSON_TYPE(related_entry_ids) = 'array'),
    supersedes_entry_id TEXT,
    tags TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(tags) AND JSON_TYPE(tags) = 'array'),
    confidence REAL,
    citations TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(citations) AND JSON_TYPE(citations) = 'array'),
    payload TEXT NOT NULL CHECK (JSON_VALID(payload)),
    PRIMARY KEY (project_id, entry_id, revision),
    -- Redundant with the PK for per-project lookups, kept deliberately: it
    -- enforces a globally unique (entry_id, revision) pair, so a revision is
    -- addressable across projects without the project_id prefix.
    UNIQUE (entry_id, revision),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

INSERT INTO project_brain_entries_new (
    project_id, entry_id, revision, entry_kind, title, rationale, status,
    author, recorded_at, related_task_ids, related_entry_ids,
    supersedes_entry_id, tags, confidence, citations, payload
)
SELECT
    project_id, entry_id, revision, entry_kind, title, rationale, status,
    author, recorded_at, related_task_ids, related_entry_ids,
    supersedes_entry_id, tags, confidence, citations, payload
FROM project_brain_entries;
DROP TABLE project_brain_entries;
ALTER TABLE project_brain_entries_new RENAME TO project_brain_entries;

CREATE INDEX idx_project_brain_current
ON project_brain_entries (project_id, entry_id, revision DESC);
CREATE INDEX idx_project_brain_kind
ON project_brain_entries (project_id, entry_kind);
CREATE INDEX idx_project_brain_status
ON project_brain_entries (project_id, status);
CREATE INDEX idx_project_brain_recorded
ON project_brain_entries (project_id, recorded_at DESC);
CREATE INDEX idx_project_brain_author
ON project_brain_entries (project_id, author);

CREATE TABLE ab_tests_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    name TEXT NOT NULL CHECK (LENGTH(TRIM(name)) > 0),
    status TEXT NOT NULL CHECK (LENGTH(TRIM(status)) > 0),
    variants TEXT NOT NULL CHECK (JSON_VALID(variants)),
    created_at TEXT NOT NULL CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '%+00:00' OR updated_at LIKE '%Z')
);

INSERT INTO ab_tests_new (id, name, status, variants, created_at, updated_at)
SELECT id, name, status, variants, created_at, updated_at FROM ab_tests;
DROP TABLE ab_tests;
ALTER TABLE ab_tests_new RENAME TO ab_tests;

CREATE INDEX idx_ab_tests_created_id ON ab_tests (created_at DESC, id ASC);

CREATE TABLE pruning_requests_new (
    agent_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(agent_id)) > 0),
    id TEXT NOT NULL CHECK (LENGTH(TRIM(id)) > 0),
    agent_name TEXT NOT NULL CHECK (LENGTH(TRIM(agent_name)) > 0),
    evaluation TEXT NOT NULL CHECK (JSON_VALID(evaluation)),
    approval_id TEXT NOT NULL CHECK (LENGTH(TRIM(approval_id)) > 0),
    status TEXT NOT NULL CHECK (LENGTH(TRIM(status)) > 0),
    created_at TEXT NOT NULL CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    decided_at TEXT CHECK (decided_at IS NULL OR decided_at LIKE '%+00:00' OR decided_at LIKE '%Z'),
    decided_by TEXT
);

INSERT INTO pruning_requests_new (
    agent_id, id, agent_name, evaluation, approval_id, status,
    created_at, decided_at, decided_by
)
SELECT
    agent_id, id, agent_name, evaluation, approval_id, status,
    created_at, decided_at, decided_by
FROM pruning_requests;
DROP TABLE pruning_requests;
ALTER TABLE pruning_requests_new RENAME TO pruning_requests;

CREATE INDEX idx_pruning_requests_created_agent
ON pruning_requests (created_at ASC, agent_id ASC);
