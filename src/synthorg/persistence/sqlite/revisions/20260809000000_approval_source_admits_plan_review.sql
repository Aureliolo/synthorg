-- Two things a record could not say, and one it should never have to.
--
-- 1. The approvals table refused the one source the plan-review gate writes.
--
-- ``ApprovalSource`` has five members; the ``source`` CHECK admitted four.
-- ``plan_review`` was never in the list, so every plan reaching human
-- review failed to persist its approval. The gap was invisible for as long
-- as it existed because no approval reached the database at all: the store
-- was constructed without a repository, held its queue in memory, and the
-- CHECK was never evaluated. The first run with durable approvals hit it
-- immediately, and the plan was failed nine milliseconds after reaching
-- PENDING_REVIEW, with no approval for an operator to decide.
--
-- 2. A task that had ever spent money could never be deleted.
--
-- Spend, metrics, approvals and decision records all name the task they are
-- about, and each pinned it with a foreign key. That made every one of them
-- a veto: a live run could not delete a project because one of its tasks
-- had recorded a cost. The pins go, and the identifier stays exactly as
-- written, matching ``cost_records.project_id`` which has never carried one
-- for the same reason: a record of something that really happened must not
-- be able to refuse the removal of what it happened to.
--
-- ``plans.parent_task_id`` keeps its RESTRICT. It is not history about a
-- task, it is a live plan built from one, and the teardown already removes
-- plans before tasks so it never fires.
--
-- 3. So a retained identifier still resolves.
--
-- ``deleted_entities`` records what a deleted task, plan or project was,
-- who removed it and when. Only a person deletes an entity, so only a
-- person's action writes here, and ``deleted_by`` is NOT NULL.
--
-- SQLite can neither alter a CHECK nor drop a foreign key, so each affected
-- table is rebuilt (create-new, copy, drop, rename) and its indexes
-- recreated. Nothing references these four by foreign key, so the drops
-- fire no cascades.

CREATE TABLE approvals_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    action_type TEXT NOT NULL CHECK (LENGTH(TRIM(action_type)) > 0),
    title TEXT NOT NULL CHECK (LENGTH(TRIM(title)) > 0),
    description TEXT NOT NULL,
    requested_by TEXT NOT NULL CHECK (LENGTH(TRIM(requested_by)) > 0),
    risk_level TEXT NOT NULL DEFAULT 'medium' CHECK (
        risk_level IN ('low', 'medium', 'high', 'critical')
    ),
    source TEXT NOT NULL DEFAULT 'review_gate' CHECK (
        source IN (
            'parked_context', 'review_gate',
            'conversational_intake', 'conversational_invite',
            'plan_review'
        )
    ),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'expired')
    ),
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    expires_at TEXT CHECK (
        expires_at IS NULL OR expires_at LIKE '%+00:00' OR expires_at LIKE '%Z'
    ),
    decided_at TEXT CHECK (
        decided_at IS NULL OR decided_at LIKE '%+00:00' OR decided_at LIKE '%Z'
    ),
    decided_by TEXT,
    decision_reason TEXT,
    task_id TEXT,
    evidence_package TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    consumed_at TEXT CHECK (
        consumed_at IS NULL OR consumed_at LIKE '%+00:00' OR consumed_at LIKE '%Z'
    ),
    CHECK (
        (decided_at IS NULL AND decided_by IS NULL)
        OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    CHECK (
        status != 'rejected' OR (decision_reason IS NOT NULL AND LENGTH(TRIM(decision_reason)) > 0)
    )
);

INSERT INTO approvals_new (
    id, action_type, title, description, requested_by, risk_level, source,
    status, created_at, expires_at, decided_at, decided_by, decision_reason,
    task_id, evidence_package, metadata, consumed_at
)
SELECT
    id,
    action_type,
    title,
    description,
    requested_by,
    risk_level,
    source,
    status,
    created_at,
    expires_at,
    decided_at,
    decided_by,
    decision_reason,
    task_id,
    evidence_package,
    metadata,
    consumed_at
FROM approvals;

DROP TABLE approvals;

ALTER TABLE approvals_new RENAME TO approvals;

CREATE INDEX idx_approvals_status ON approvals (status);
CREATE INDEX idx_approvals_action_type ON approvals (action_type);
CREATE INDEX idx_approvals_risk_level ON approvals (risk_level);
CREATE INDEX idx_approvals_requested_by_status ON approvals (requested_by, status);
CREATE INDEX idx_approvals_status_expires_at ON approvals (status, expires_at);
CREATE INDEX idx_approvals_task_id ON approvals (task_id);
CREATE INDEX idx_approvals_status_created_at
ON approvals (status, created_at DESC);
CREATE INDEX idx_approvals_risk_created_at
ON approvals (risk_level, created_at DESC);
CREATE INDEX idx_approvals_action_created_at
ON approvals (action_type, created_at DESC);

CREATE TABLE cost_records_new (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT,
    task_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    timestamp TEXT NOT NULL,
    call_category TEXT,
    prompt_class_id TEXT,
    claim_id TEXT,
    project_id TEXT
);

INSERT INTO cost_records_new (
    rowid, agent_id, task_id, provider, model, input_tokens, output_tokens,
    cost, currency, timestamp, call_category, prompt_class_id, claim_id,
    project_id
)
SELECT
    rowid,
    agent_id,
    task_id,
    provider,
    model,
    input_tokens,
    output_tokens,
    cost,
    currency,
    timestamp,
    call_category,
    prompt_class_id,
    claim_id,
    project_id
FROM cost_records;

DROP TABLE cost_records;

ALTER TABLE cost_records_new RENAME TO cost_records;

CREATE INDEX idx_cost_records_agent_id ON cost_records (agent_id);
CREATE INDEX idx_cost_records_task_id ON cost_records (task_id);
CREATE INDEX idx_cost_records_timestamp ON cost_records (timestamp DESC);
CREATE INDEX idx_cost_records_agent_timestamp
ON cost_records (agent_id, timestamp DESC);
CREATE INDEX idx_cost_records_task_timestamp
ON cost_records (task_id, timestamp DESC);
CREATE INDEX idx_cost_records_prompt_class_timestamp
ON cost_records (prompt_class_id, timestamp DESC);
CREATE UNIQUE INDEX idx_cost_records_claim_id
ON cost_records (claim_id, timestamp);
CREATE INDEX idx_cost_records_project_timestamp
ON cost_records (project_id, timestamp DESC);

CREATE TABLE task_metrics_new (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    is_success INTEGER NOT NULL,
    duration_seconds REAL,
    cost REAL,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    turns_used INTEGER,
    tokens_used INTEGER,
    quality_score REAL,
    complexity TEXT NOT NULL,
    run_outcome TEXT
    CHECK (run_outcome IN ('succeeded', 'empty', 'failed'))
);

INSERT INTO task_metrics_new (
    id, agent_id, task_id, task_type, completed_at, is_success,
    duration_seconds, cost, currency, turns_used, tokens_used, quality_score,
    complexity, run_outcome
)
SELECT
    id,
    agent_id,
    task_id,
    task_type,
    completed_at,
    is_success,
    duration_seconds,
    cost,
    currency,
    turns_used,
    tokens_used,
    quality_score,
    complexity,
    run_outcome
FROM task_metrics;

DROP TABLE task_metrics;

ALTER TABLE task_metrics_new RENAME TO task_metrics;

CREATE INDEX idx_tm_agent_id ON task_metrics (agent_id);
CREATE INDEX idx_tm_completed_at ON task_metrics (completed_at);
CREATE INDEX idx_tm_agent_completed
ON task_metrics (agent_id, completed_at);
CREATE INDEX idx_tm_task_id ON task_metrics (task_id);

CREATE TABLE decision_records_new (
    id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    approval_id TEXT,
    executing_agent_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL CHECK (reviewer_agent_id != executing_agent_id),
    decision TEXT NOT NULL CHECK (decision IN (
        'approved', 'rejected', 'auto_approved', 'auto_rejected', 'escalated'
    )),
    reason TEXT,
    criteria_snapshot TEXT NOT NULL DEFAULT '[]',
    recorded_at TEXT NOT NULL CHECK (
        recorded_at LIKE '%+00:00' OR recorded_at LIKE '%Z'
    ),
    version INTEGER NOT NULL CHECK (version >= 1),
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE (task_id, version)
);

INSERT INTO decision_records_new (
    id, task_id, approval_id, executing_agent_id, reviewer_agent_id,
    decision, reason, criteria_snapshot, recorded_at, version, metadata
)
SELECT
    id,
    task_id,
    approval_id,
    executing_agent_id,
    reviewer_agent_id,
    decision,
    reason,
    criteria_snapshot,
    recorded_at,
    version,
    metadata
FROM decision_records;

DROP TABLE decision_records;

ALTER TABLE decision_records_new RENAME TO decision_records;

CREATE INDEX idx_dr_executing_agent_recorded
ON decision_records (executing_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_reviewer_agent_recorded
ON decision_records (reviewer_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_task_recorded_id
ON decision_records (task_id, recorded_at, id);

CREATE TABLE deleted_entities (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('task', 'plan', 'project')),
    entity_id TEXT NOT NULL CHECK (LENGTH(TRIM(entity_id)) > 0),
    display_name TEXT NOT NULL CHECK (LENGTH(TRIM(display_name)) > 0),
    deleted_by TEXT NOT NULL CHECK (LENGTH(TRIM(deleted_by)) > 0),
    deleted_at TEXT NOT NULL CHECK (
        deleted_at LIKE '%+00:00' OR deleted_at LIKE '%Z'
    )
);

CREATE INDEX idx_deleted_entities_lookup
ON deleted_entities (entity_kind, entity_id, deleted_at DESC);
