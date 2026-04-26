-- SynthOrg Postgres schema -- single source of truth for the postgres backend.
--
-- This file defines the desired database state for Postgres. Atlas diffs it
-- against the current DB to generate versioned migrations.
-- Do NOT execute this file directly -- use `atlas migrate diff --env postgres`.
--
-- This is the Postgres-native sibling of src/synthorg/persistence/sqlite/schema.sql.
-- Both schemas describe the same logical data model but use each engine's
-- native types:
--   * JSONB for fields that SQLite stores as TEXT + json.dumps
--   * TIMESTAMPTZ for fields that SQLite stores as TEXT + ISO8601 strings
--   * BOOLEAN for fields that SQLite stores as INTEGER 0/1
--   * BIGINT / DOUBLE PRECISION for SQLite INTEGER / REAL
--   * BIGINT GENERATED ALWAYS AS IDENTITY for AUTOINCREMENT rowids
--
-- Postgres-native features the SQLite schema cannot mirror:
--   * GIN indexes over JSONB columns
--     (audit_entries.matched_rules, messages.metadata,
--      lifecycle_events.metadata, conflict_escalations.conflict_json).
--     The Postgres-only ``query_jsonb_contains`` /
--     ``query_jsonb_key_exists`` capability protocol exposes these.
--   * ``CONSTRAINT TRIGGER`` enforcement of HR invariants
--     (enforce_ceo_minimum, enforce_owner_minimum) that SQLite
--     enforces with simpler ``BEFORE UPDATE/DELETE`` triggers.
--   * Partial unique indexes such as
--     ``idx_ftc_single_active WHERE is_active = TRUE``; SQLite mirrors
--     the same shape via ``WHERE is_active = 1``, but the boolean type
--     prefix differs.
--
-- See the SQLite schema header for the column-level inventory of
-- TEXT-vs-JSONB, INTEGER-vs-BOOLEAN, and TEXT-vs-TIMESTAMPTZ pairings.
--
-- Repositories at the Python level return identical Pydantic models from
-- both backends; only the wire serialization differs, and the conformance
-- suite at tests/conformance/persistence/ exercises every repository
-- against both backends to keep drift honest.

-- ── Tasks ─────────────────────────────────────────────────────
CREATE TABLE tasks (
    id TEXT NOT NULL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    project TEXT NOT NULL,
    created_by TEXT NOT NULL,
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    estimated_complexity TEXT NOT NULL DEFAULT 'medium',
    budget_limit DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    deadline TIMESTAMPTZ,
    max_retries BIGINT NOT NULL DEFAULT 1,
    parent_task_id TEXT,
    task_structure JSONB,
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    reviewers JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifacts_expected JSONB NOT NULL DEFAULT '[]'::jsonb,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    delegation_chain JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_assigned_to ON tasks(assigned_to);
CREATE INDEX idx_tasks_project ON tasks(project);

-- ── Cost records ──────────────────────────────────────────────
-- Composite (rowid, timestamp) primary key: TimescaleDB hypertables
-- require the partitioning column to appear in every unique index.
-- On deployments where TimescaleDB is available, the Postgres backend
-- converts this table to a hypertable at runtime during its
-- post-Atlas setup step; on vanilla Postgres the composite PK is
-- functionally equivalent to the old ``PRIMARY KEY (rowid)`` because
-- ``rowid`` is still globally unique via the IDENTITY sequence.
CREATE TABLE cost_records (
    rowid BIGINT GENERATED ALWAYS AS IDENTITY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens BIGINT NOT NULL,
    output_tokens BIGINT NOT NULL,
    cost DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
        CHECK (currency ~ '^[A-Z]{3}$'),
    timestamp TIMESTAMPTZ NOT NULL,
    call_category TEXT,
    PRIMARY KEY (rowid, timestamp)
);

CREATE INDEX idx_cost_records_agent_id ON cost_records(agent_id);
CREATE INDEX idx_cost_records_task_id ON cost_records(task_id);
CREATE INDEX idx_cost_records_timestamp ON cost_records(timestamp DESC);

-- ── Messages ──────────────────────────────────────────────────
CREATE TABLE messages (
    id TEXT NOT NULL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    sender TEXT NOT NULL,
    "to" TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    channel TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_messages_channel ON messages(channel);
CREATE INDEX idx_messages_timestamp ON messages(timestamp);
CREATE INDEX idx_messages_metadata_gin ON messages USING GIN (metadata);
CREATE INDEX idx_messages_sender ON messages(sender);
CREATE INDEX idx_messages_to ON messages("to");

-- ── Lifecycle events ──────────────────────────────────────────
CREATE TABLE lifecycle_events (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    initiated_by TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_le_agent_id ON lifecycle_events(agent_id);
CREATE INDEX idx_le_event_type ON lifecycle_events(event_type);
CREATE INDEX idx_le_timestamp ON lifecycle_events(timestamp);
CREATE INDEX idx_le_metadata_gin ON lifecycle_events USING GIN (metadata);

-- ── Task metrics ──────────────────────────────────────────────
CREATE TABLE task_metrics (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    task_type TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    is_success BOOLEAN NOT NULL,
    duration_seconds DOUBLE PRECISION NOT NULL,
    cost DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
        CHECK (currency ~ '^[A-Z]{3}$'),
    turns_used BIGINT NOT NULL,
    tokens_used BIGINT NOT NULL,
    quality_score DOUBLE PRECISION,
    complexity TEXT NOT NULL
);

CREATE INDEX idx_tm_agent_id ON task_metrics(agent_id);
CREATE INDEX idx_tm_completed_at ON task_metrics(completed_at);
CREATE INDEX idx_tm_agent_completed
    ON task_metrics(agent_id, completed_at);

-- ── Collaboration metrics ─────────────────────────────────────
CREATE TABLE collaboration_metrics (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    delegation_success BOOLEAN,
    delegation_response_seconds DOUBLE PRECISION,
    conflict_constructiveness DOUBLE PRECISION,
    meeting_contribution DOUBLE PRECISION,
    loop_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    handoff_completeness DOUBLE PRECISION
);

CREATE INDEX idx_cm_agent_id ON collaboration_metrics(agent_id);
CREATE INDEX idx_cm_recorded_at
    ON collaboration_metrics(recorded_at);
CREATE INDEX idx_cm_agent_recorded
    ON collaboration_metrics(agent_id, recorded_at);

-- ── Parked contexts ───────────────────────────────────────────
CREATE TABLE parked_contexts (
    id TEXT NOT NULL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT,
    approval_id TEXT NOT NULL,
    parked_at TIMESTAMPTZ NOT NULL,
    context_json JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_pc_agent_id ON parked_contexts(agent_id);
CREATE INDEX idx_pc_approval_id ON parked_contexts(approval_id);

-- ── Audit entries ─────────────────────────────────────────────
-- Composite (id, timestamp) primary key for TimescaleDB compatibility
-- (see ``cost_records`` above for the rationale).
CREATE TABLE audit_entries (
    id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    agent_id TEXT,
    task_id TEXT,
    tool_name TEXT NOT NULL,
    tool_category TEXT NOT NULL,
    action_type TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    verdict TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    matched_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
    evaluation_duration_ms DOUBLE PRECISION NOT NULL,
    approval_id TEXT,
    PRIMARY KEY (id, timestamp)
);

CREATE INDEX idx_ae_timestamp ON audit_entries(timestamp);
CREATE INDEX idx_ae_agent_id ON audit_entries(agent_id);
CREATE INDEX idx_ae_action_type ON audit_entries(action_type);
CREATE INDEX idx_ae_verdict ON audit_entries(verdict);
CREATE INDEX idx_ae_risk_level ON audit_entries(risk_level);
CREATE INDEX idx_ae_matched_rules_gin
    ON audit_entries USING GIN (matched_rules);

-- ── Settings (namespaced key-value) ───────────────────────────
CREATE TABLE settings (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, key)
);

-- ── Users ─────────────────────────────────────────────────────
CREATE TABLE users (
    id TEXT NOT NULL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    org_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    scoped_departments JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_users_role ON users(role);
CREATE UNIQUE INDEX idx_single_ceo ON users(role) WHERE role = 'ceo';

-- Prevent removing the last CEO via role change.
--
-- Takes a transaction-scoped advisory lock so two concurrent
-- transactions cannot both see each other's pre-commit CEO row
-- as still-present.  The lock id (42_001) is arbitrary but stable;
-- the sibling enforce_owner_minimum uses a disjoint id so the two
-- triggers never contend with each other.
CREATE FUNCTION enforce_ceo_minimum() RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(42001);
    IF NOT EXISTS (SELECT 1 FROM users WHERE role = 'ceo') THEN
        RAISE EXCEPTION 'Cannot remove the last CEO'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_enforce_ceo_minimum
    AFTER UPDATE OF role ON users
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.role = 'ceo' AND NEW.role != 'ceo')
    EXECUTE FUNCTION enforce_ceo_minimum();

-- Prevent removing the last owner via org_roles change.
--
-- See ``enforce_ceo_minimum`` above for the rationale.  Uses a
-- disjoint advisory-lock id so the two triggers do not serialise
-- against one another unnecessarily.
CREATE FUNCTION enforce_owner_minimum() RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(42002);
    IF NOT EXISTS (
        SELECT 1 FROM users WHERE org_roles @> '["owner"]'::jsonb
    ) THEN
        RAISE EXCEPTION 'Cannot remove the last owner'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_enforce_owner_minimum
    AFTER UPDATE OF org_roles ON users
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.org_roles @> '["owner"]'::jsonb
          AND NOT NEW.org_roles @> '["owner"]'::jsonb)
    EXECUTE FUNCTION enforce_owner_minimum();

CREATE CONSTRAINT TRIGGER trg_enforce_ceo_minimum_delete
    AFTER DELETE ON users
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.role = 'ceo')
    EXECUTE FUNCTION enforce_ceo_minimum();

CREATE CONSTRAINT TRIGGER trg_enforce_owner_minimum_delete
    AFTER DELETE ON users
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.org_roles @> '["owner"]'::jsonb)
    EXECUTE FUNCTION enforce_owner_minimum();

-- ── API keys ──────────────────────────────────────────────────
CREATE TABLE api_keys (
    id TEXT NOT NULL PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_api_keys_user_id ON api_keys(user_id);

-- ── Sessions ─────────────────────────────────────────────────
CREATE TABLE sessions (
    session_id TEXT NOT NULL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    last_active_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_sessions_user_revoked_expires
    ON sessions(user_id, revoked, expires_at);
CREATE INDEX idx_sessions_revoked_expires
    ON sessions(revoked, expires_at);
CREATE INDEX idx_sessions_expires_at ON sessions(expires_at);

-- ── Checkpoints ───────────────────────────────────────────────
CREATE TABLE checkpoints (
    id TEXT NOT NULL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    turn_number BIGINT NOT NULL CHECK (turn_number >= 0),
    context_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_cp_execution_id ON checkpoints(execution_id);
CREATE INDEX idx_cp_task_id ON checkpoints(task_id);
CREATE INDEX idx_cp_exec_turn
    ON checkpoints(execution_id, turn_number);
CREATE INDEX idx_cp_task_turn
    ON checkpoints(task_id, turn_number);

-- ── Heartbeats ────────────────────────────────────────────────
CREATE TABLE heartbeats (
    execution_id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_hb_last_heartbeat
    ON heartbeats(last_heartbeat_at);

-- ── Agent states ──────────────────────────────────────────────
CREATE TABLE agent_states (
    agent_id TEXT NOT NULL PRIMARY KEY,
    execution_id TEXT,
    task_id TEXT,
    status TEXT NOT NULL DEFAULT 'idle'
        CHECK (status IN ('idle', 'executing', 'paused')),
    turn_count BIGINT NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
    accumulated_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (accumulated_cost >= 0.0),
    currency TEXT NOT NULL DEFAULT 'USD'
        CHECK (currency ~ '^[A-Z]{3}$'),
    last_activity_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    CHECK (
        (status = 'idle'
         AND execution_id IS NULL
         AND task_id IS NULL
         AND started_at IS NULL
         AND turn_count = 0
         AND accumulated_cost = 0.0)
        OR
        (status IN ('executing', 'paused')
         AND execution_id IS NOT NULL
         AND started_at IS NOT NULL)
    )
);

CREATE INDEX idx_as_status_activity
    ON agent_states(status, last_activity_at DESC);

-- ── Artifacts ────────────────────────────────────────────────
CREATE TABLE artifacts (
    id TEXT NOT NULL PRIMARY KEY,
    type TEXT NOT NULL,
    path TEXT NOT NULL,
    task_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    size_bytes BIGINT NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    project_id TEXT
);

CREATE INDEX idx_artifacts_task_id ON artifacts(task_id);
CREATE INDEX idx_artifacts_created_by ON artifacts(created_by);
CREATE INDEX idx_artifacts_type ON artifacts(type);
CREATE INDEX idx_artifacts_project_id ON artifacts(project_id);

-- ── Projects ─────────────────────────────────────────────────
CREATE TABLE projects (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    team JSONB NOT NULL DEFAULT '[]'::jsonb,
    lead TEXT,
    task_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    deadline TIMESTAMPTZ,
    budget DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (budget >= 0.0),
    status TEXT NOT NULL DEFAULT 'planning'
);

CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_projects_lead ON projects(lead);

-- ── Project-lifetime cost aggregates ─────────────────────────
CREATE TABLE project_cost_aggregates (
    project_id TEXT NOT NULL PRIMARY KEY CHECK (length(project_id) > 0),
    total_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (total_cost >= 0.0),
    total_input_tokens BIGINT NOT NULL DEFAULT 0
        CHECK (total_input_tokens >= 0),
    total_output_tokens BIGINT NOT NULL DEFAULT 0
        CHECK (total_output_tokens >= 0),
    record_count BIGINT NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    last_updated TIMESTAMPTZ NOT NULL
);

-- ── Custom personality presets (user-defined) ────────────────
CREATE TABLE custom_presets (
    name TEXT NOT NULL PRIMARY KEY CHECK (length(name) > 0),
    config_json JSONB NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE workflow_definitions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    name TEXT NOT NULL CHECK (length(name) > 0),
    description TEXT NOT NULL DEFAULT '',
    workflow_type TEXT NOT NULL CHECK (workflow_type IN (
        'sequential_pipeline', 'parallel_execution', 'kanban', 'agile_kanban'
    )),
    version TEXT NOT NULL DEFAULT '1.0.0' CHECK (length(version) > 0),
    inputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    outputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_subworkflow BOOLEAN NOT NULL DEFAULT FALSE,
    nodes JSONB NOT NULL,
    edges JSONB NOT NULL,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1)
);

CREATE INDEX idx_wd_workflow_type
    ON workflow_definitions(workflow_type);

CREATE INDEX idx_wd_updated_at
    ON workflow_definitions(updated_at DESC);

CREATE INDEX idx_wd_is_subworkflow
    ON workflow_definitions(is_subworkflow);

-- ── Subworkflows ─────────────────────────────────────────────

CREATE TABLE subworkflows (
    subworkflow_id TEXT NOT NULL CHECK(length(subworkflow_id) > 0),
    semver TEXT NOT NULL CHECK(length(semver) > 0),
    name TEXT NOT NULL CHECK(length(name) > 0),
    description TEXT NOT NULL DEFAULT '',
    workflow_type TEXT NOT NULL CHECK(workflow_type IN (
        'sequential_pipeline', 'parallel_execution', 'kanban', 'agile_kanban'
    )),
    inputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    outputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    nodes JSONB NOT NULL,
    edges JSONB NOT NULL,
    created_by TEXT NOT NULL CHECK(length(created_by) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (subworkflow_id, semver)
);

CREATE INDEX idx_subworkflows_id
    ON subworkflows(subworkflow_id);

CREATE INDEX idx_subworkflows_created_at
    ON subworkflows(created_at DESC);

CREATE INDEX idx_subworkflows_updated_at
    ON subworkflows(updated_at DESC);

-- ── Workflow execution instances ─────────────────────────────

CREATE TABLE workflow_executions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    definition_id TEXT NOT NULL CHECK (length(definition_id) > 0),
    definition_revision BIGINT NOT NULL CHECK (definition_revision >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'completed', 'failed', 'cancelled'
    )),
    node_executions JSONB NOT NULL DEFAULT '[]'::jsonb,
    activated_by TEXT NOT NULL CHECK (length(activated_by) > 0),
    project TEXT NOT NULL CHECK (length(project) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error TEXT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    FOREIGN KEY (definition_id) REFERENCES workflow_definitions(id)
);

CREATE INDEX idx_wfe_definition_id
    ON workflow_executions(definition_id);

CREATE INDEX idx_wfe_status
    ON workflow_executions(status);

CREATE INDEX idx_wfe_updated_at
    ON workflow_executions(updated_at DESC);

CREATE INDEX idx_wfe_definition_updated
    ON workflow_executions(definition_id, updated_at DESC);

CREATE INDEX idx_wfe_status_updated
    ON workflow_executions(status, updated_at DESC);

CREATE INDEX idx_wfe_project
    ON workflow_executions(project);

CREATE INDEX idx_wfe_definition_revision
    ON workflow_executions(definition_id, definition_revision);

-- ── Fine-tuning pipeline runs ───────────────────────────────────
CREATE TABLE fine_tune_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    stage TEXT NOT NULL CHECK (stage IN (
        'idle', 'generating_data', 'mining_negatives', 'training',
        'evaluating', 'deploying', 'complete', 'failed'
    )),
    progress DOUBLE PRECISION
        CHECK (progress IS NULL OR (progress >= 0.0 AND progress <= 1.0)),
    error TEXT,
    config_json JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    stages_completed JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX idx_ftr_stage
    ON fine_tune_runs(stage);

CREATE INDEX idx_ftr_started_at
    ON fine_tune_runs(started_at DESC);

CREATE INDEX idx_ftr_updated_at
    ON fine_tune_runs(updated_at DESC);

-- ── Fine-tuning checkpoints ─────────────────────────────────────
CREATE TABLE fine_tune_checkpoints (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) > 0),
    run_id TEXT NOT NULL REFERENCES fine_tune_runs(id) ON DELETE CASCADE,
    model_path TEXT NOT NULL,
    base_model TEXT NOT NULL,
    doc_count BIGINT NOT NULL CHECK (doc_count >= 0),
    eval_metrics_json JSONB,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    backup_config_json JSONB
);

CREATE INDEX idx_ftc_run_id
    ON fine_tune_checkpoints(run_id);

CREATE INDEX idx_ftc_active
    ON fine_tune_checkpoints(is_active);

CREATE UNIQUE INDEX idx_ftc_single_active
    ON fine_tune_checkpoints(is_active)
    WHERE is_active = TRUE;

CREATE INDEX idx_ftc_created_at
    ON fine_tune_checkpoints(created_at DESC);

-- ── Workflow Definition Versions ─────────────────────────────

CREATE TABLE workflow_definition_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_wdv_entity_saved
    ON workflow_definition_versions(entity_id, saved_at DESC);
CREATE INDEX idx_wdv_content_hash
    ON workflow_definition_versions(entity_id, content_hash);

-- ── Decision records (auditable decisions drop-box) ─────────────
CREATE TABLE decision_records (
    id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    approval_id TEXT,
    executing_agent_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL
        CHECK (reviewer_agent_id != executing_agent_id),
    decision TEXT NOT NULL CHECK (decision IN (
        'approved', 'rejected', 'auto_approved', 'auto_rejected', 'escalated'
    )),
    reason TEXT,
    criteria_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL CHECK (version >= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(task_id, version)
);

CREATE INDEX idx_dr_executing_agent_recorded
    ON decision_records(executing_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_reviewer_agent_recorded
    ON decision_records(reviewer_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_metadata_gin
    ON decision_records USING GIN (metadata);

-- ── Login Attempts (account lockout) ─────────────────────────
CREATE TABLE login_attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL,
    ip_address TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_la_username_attempted
    ON login_attempts(username, attempted_at);
CREATE INDEX idx_la_attempted_at
    ON login_attempts(attempted_at);

-- ── Refresh Tokens ───────────────────────────────────────────
CREATE TABLE refresh_tokens (
    token_hash TEXT NOT NULL PRIMARY KEY,
    session_id TEXT NOT NULL
        REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_rt_user_id ON refresh_tokens(user_id);
CREATE INDEX idx_rt_session_id ON refresh_tokens(session_id);
CREATE INDEX idx_rt_expires_at ON refresh_tokens(expires_at);

-- ── Risk tier overrides ─────────────────────────────────────
CREATE TABLE risk_overrides (
    id TEXT NOT NULL PRIMARY KEY,
    action_type TEXT NOT NULL,
    original_tier TEXT NOT NULL,
    override_tier TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT,
    CHECK (
        (revoked_at IS NULL AND revoked_by IS NULL)
        OR
        (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)
    )
);

CREATE INDEX idx_ro_action_type ON risk_overrides(action_type);
CREATE INDEX idx_ro_active
    ON risk_overrides(created_at DESC, expires_at)
    WHERE revoked_at IS NULL;

-- ── SSRF violations ─────────────────────────────────────────
CREATE TABLE ssrf_violations (
    id TEXT NOT NULL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    url TEXT NOT NULL,
    hostname TEXT NOT NULL,
    port BIGINT NOT NULL CHECK (port BETWEEN 1 AND 65535),
    resolved_ip TEXT,
    blocked_range TEXT,
    provider_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'allowed', 'denied')),
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    CHECK (
        (status = 'pending' AND resolved_by IS NULL AND resolved_at IS NULL)
        OR
        (status IN ('allowed', 'denied')
         AND resolved_by IS NOT NULL
         AND resolved_at IS NOT NULL)
    )
);

CREATE INDEX idx_sv_status_timestamp
    ON ssrf_violations(status, timestamp DESC);
CREATE INDEX idx_sv_timestamp ON ssrf_violations(timestamp);
CREATE INDEX idx_sv_hostname ON ssrf_violations(hostname, port);

-- ── Agent identity versions ────────────────────────────────────
CREATE TABLE agent_identity_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_aiv_entity_saved
    ON agent_identity_versions(entity_id, saved_at DESC);
CREATE INDEX idx_aiv_content_hash
    ON agent_identity_versions(entity_id, content_hash);

-- ── Evaluation config versions ────────────────────────────────────

CREATE TABLE evaluation_config_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_ecv_entity_saved
    ON evaluation_config_versions(entity_id, saved_at DESC);
CREATE INDEX idx_ecv_content_hash
    ON evaluation_config_versions(entity_id, content_hash);

-- ── Budget config versions ───────────────────────────────────────

CREATE TABLE budget_config_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_bcv_entity_saved
    ON budget_config_versions(entity_id, saved_at DESC);
CREATE INDEX idx_bcv_content_hash
    ON budget_config_versions(entity_id, content_hash);

-- ── Company versions ─────────────────────────────────────────────

CREATE TABLE company_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_cv_entity_saved
    ON company_versions(entity_id, saved_at DESC);
CREATE INDEX idx_cv_content_hash
    ON company_versions(entity_id, content_hash);

-- ── Role versions ────────────────────────────────────────────────

CREATE TABLE role_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_rv_entity_saved
    ON role_versions(entity_id, saved_at DESC);
CREATE INDEX idx_rv_content_hash
    ON role_versions(entity_id, content_hash);

-- ── Circuit breaker state ─────────────────────────────────────────

CREATE TABLE circuit_breaker_state (
    pair_key_a TEXT NOT NULL CHECK (length(pair_key_a) > 0),
    pair_key_b TEXT NOT NULL CHECK (length(pair_key_b) > 0),
    bounce_count BIGINT NOT NULL DEFAULT 0 CHECK (bounce_count >= 0),
    trip_count BIGINT NOT NULL DEFAULT 0 CHECK (trip_count >= 0),
    opened_at DOUBLE PRECISION,
    PRIMARY KEY (pair_key_a, pair_key_b)
);

-- ── Ontology: Entity definitions ──────────────────────────────

CREATE TABLE entity_definitions (
    name TEXT NOT NULL PRIMARY KEY CHECK (length(name) > 0),
    tier TEXT NOT NULL CHECK (tier IN ('core', 'user')),
    source TEXT NOT NULL CHECK (source IN ('auto', 'config', 'api')),
    definition TEXT NOT NULL DEFAULT '',
    fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    constraints JSONB NOT NULL DEFAULT '[]'::jsonb,
    disambiguation TEXT NOT NULL DEFAULT '',
    relationships JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_ed_tier
    ON entity_definitions(tier);

-- ── Ontology: Entity definition versions ──────────────────────

CREATE TABLE entity_definition_versions (
    entity_id TEXT NOT NULL CHECK (length(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (length(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_edv_entity_saved
    ON entity_definition_versions(entity_id, saved_at DESC);
CREATE INDEX idx_edv_content_hash
    ON entity_definition_versions(entity_id, content_hash);

-- ── Connection secrets ───────────────────────────────────────
CREATE TABLE connection_secrets (
    secret_id TEXT NOT NULL PRIMARY KEY CHECK (length(secret_id) > 0),
    encrypted_value BYTEA NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1 CHECK (key_version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    rotated_at TIMESTAMPTZ
);

-- ── Connections ──────────────────────────────────────────────
CREATE TABLE connections (
    name TEXT NOT NULL PRIMARY KEY CHECK (length(name) > 0),
    connection_type TEXT NOT NULL CHECK (
        connection_type IN (
            'github', 'slack', 'smtp', 'database',
            'generic_http', 'oauth_app'
        )
    ),
    auth_method TEXT NOT NULL CHECK (
        auth_method IN (
            'api_key', 'oauth2', 'basic_auth',
            'bearer_token', 'custom'
        )
    ),
    base_url TEXT,
    secret_refs_json JSONB NOT NULL DEFAULT '[]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0
        CHECK (rate_limit_rpm >= 0),
    rate_limit_concurrent INTEGER NOT NULL DEFAULT 0
        CHECK (rate_limit_concurrent >= 0),
    health_check_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (
            health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')
        ),
    last_health_check_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_connections_type ON connections(connection_type);

-- ── OAuth states ─────────────────────────────────────────────
CREATE TABLE oauth_states (
    state_token TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections(name) ON DELETE CASCADE,
    pkce_verifier TEXT,
    scopes_requested TEXT NOT NULL DEFAULT '',
    redirect_uri TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_oauth_states_expires ON oauth_states(expires_at);
CREATE INDEX idx_oauth_states_connection ON oauth_states(connection_name);

-- ── Webhook receipts ─────────────────────────────────────────
CREATE TABLE webhook_receipts (
    id TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections(name) ON DELETE CASCADE,
    event_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'received',
    received_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT
);

CREATE INDEX idx_webhook_receipts_conn_received
    ON webhook_receipts(connection_name, received_at DESC);

-- ── MCP catalog installations ────────────────────────────────
CREATE TABLE mcp_installations (
    catalog_entry_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(catalog_entry_id) > 0),
    connection_name TEXT REFERENCES connections(name) ON DELETE SET NULL,
    installed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_mcp_installations_connection
    ON mcp_installations(connection_name);

-- ── Training plans ──────────────────────────────────────────────
-- Stores training plan configuration for agent onboarding.
-- Plans transition from pending -> executed|failed after execution.
CREATE TABLE training_plans (
    id TEXT NOT NULL PRIMARY KEY,
    new_agent_id TEXT NOT NULL,
    new_agent_role TEXT NOT NULL,
    new_agent_level TEXT NOT NULL,
    new_agent_department TEXT,
    source_selector_type TEXT NOT NULL DEFAULT 'role_top_performers',
    enabled_content_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    curation_strategy_type TEXT NOT NULL DEFAULT 'relevance',
    volume_caps JSONB NOT NULL DEFAULT '[]'::jsonb,
    override_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    skip_training BOOLEAN NOT NULL DEFAULT false,
    require_review BOOLEAN NOT NULL DEFAULT true,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'executed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL,
    executed_at TIMESTAMPTZ,
    CHECK(
        (status = 'pending' AND executed_at IS NULL)
        OR (status <> 'pending' AND executed_at IS NOT NULL)
    )
);

CREATE INDEX idx_training_plans_agent_status
    ON training_plans(new_agent_id, status);
CREATE INDEX idx_training_plans_created
    ON training_plans(created_at);

-- ── Training results ────────────────────────────────────────────
-- Stores training execution outcomes with per-stage pipeline counts.
CREATE TABLE training_results (
    id TEXT NOT NULL PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES training_plans(id),
    new_agent_id TEXT NOT NULL,
    source_agents_used JSONB NOT NULL DEFAULT '[]'::jsonb,
    items_extracted JSONB NOT NULL DEFAULT '[]'::jsonb,
    items_after_curation JSONB NOT NULL DEFAULT '[]'::jsonb,
    items_after_guards JSONB NOT NULL DEFAULT '[]'::jsonb,
    items_stored JSONB NOT NULL DEFAULT '[]'::jsonb,
    approval_item_id TEXT,
    pending_approvals JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_pending BOOLEAN NOT NULL DEFAULT false,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    CHECK(completed_at >= started_at)
);

CREATE UNIQUE INDEX idx_training_results_plan
    ON training_results(plan_id);
CREATE INDEX idx_training_results_agent
    ON training_results(new_agent_id, completed_at DESC);

-- ── Conflict escalations (#1418) ───────────────────────────────
-- Human escalation approval queue: one row per conflict awaiting a
-- human decision.  Matches the SQLite sibling ``conflict_escalations``
-- but uses JSONB for payloads and TIMESTAMPTZ for timestamps.
CREATE TABLE conflict_escalations (
    id TEXT NOT NULL PRIMARY KEY,
    conflict_id TEXT NOT NULL,
    conflict_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(
        status IN ('pending', 'decided', 'expired', 'cancelled')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    decision_json JSONB,
    CHECK(length(trim(id)) > 0),
    CHECK(length(trim(conflict_id)) > 0),
    -- Payload columns must be JSON objects (not scalars, arrays,
    -- nulls, or strings), matching the SQLite sibling's
    -- ``json_type = 'object'`` invariant so both backends refuse
    -- malformed payloads at the schema layer.
    CHECK(jsonb_typeof(conflict_json) = 'object'),
    CHECK(decision_json IS NULL OR jsonb_typeof(decision_json) = 'object'),
    -- DECIDED rows carry the full decision triple; decided_by must
    -- be a nonblank actor identifier.
    CHECK(
        status != 'decided'
        OR (
            decision_json IS NOT NULL
            AND jsonb_typeof(decision_json) = 'object'
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND length(trim(decided_by)) > 0
        )
    ),
    -- PENDING rows carry no decision triple at all.
    CHECK(
        status != 'pending'
        OR (decision_json IS NULL AND decided_at IS NULL AND decided_by IS NULL)
    ),
    -- EXPIRED / CANCELLED rows drop any decision payload but MUST
    -- carry audit-trail columns (transition timestamp + nonblank
    -- actor) so auditors can always answer "who transitioned this,
    -- and when".
    CHECK(
        status NOT IN ('expired', 'cancelled')
        OR (
            decision_json IS NULL
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND length(trim(decided_by)) > 0
        )
    )
);
CREATE INDEX idx_conflict_escalations_status_created
    ON conflict_escalations(status, created_at);
CREATE INDEX idx_conflict_escalations_conflict_id
    ON conflict_escalations(conflict_id);
CREATE INDEX idx_conflict_escalations_status_expires_at
    ON conflict_escalations(status, expires_at);
-- Enforce "at most one PENDING escalation per conflict" so two
-- concurrent resolvers cannot enqueue competing queue rows for the
-- same conflict.
CREATE UNIQUE INDEX idx_conflict_escalations_unique_pending_conflict
    ON conflict_escalations(conflict_id) WHERE status = 'pending';

-- LISTEN/NOTIFY wiring for cross-instance resolver wake-up (#1418)
-- is emitted by the application (``PostgresEscalationRepository._
-- publish_notify``) using ``EscalationQueueConfig.notify_channel``
-- so operators can rename the channel without a schema change.  We
-- intentionally do NOT install a DB-side trigger: a hard-coded
-- channel in the trigger would break deployments that override the
-- notify channel, and double-publishing (trigger + app) would cause
-- duplicate wake-ups.

-- ── Custom signal rules (#1443) ─────────────────────────────────
-- Mirror of the SQLite ``custom_rules`` table; existed on SQLite
-- only until the parity sweep.  Boolean ``enabled`` uses native
-- BOOLEAN; JSON target_altitudes uses JSONB.
CREATE TABLE custom_rules (
    id TEXT NOT NULL PRIMARY KEY CHECK (length(id) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    metric_path TEXT NOT NULL CHECK (length(trim(metric_path)) > 0),
    comparator TEXT NOT NULL CHECK (length(trim(comparator)) > 0),
    threshold DOUBLE PRECISION NOT NULL,
    severity TEXT NOT NULL CHECK (length(trim(severity)) > 0),
    target_altitudes JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX custom_rules_name ON custom_rules (name);
CREATE INDEX idx_custom_rules_enabled ON custom_rules(enabled);

-- ── Approvals (#1443) ───────────────────────────────────────────
-- Mirror of the SQLite ``approvals`` table.  Boolean-ish checks
-- express the same state-machine invariants; JSONB metadata +
-- evidence_package replace TEXT blobs.
CREATE TABLE approvals (
    id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(id)) > 0),
    action_type TEXT NOT NULL CHECK (length(trim(action_type)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT NOT NULL,
    requested_by TEXT NOT NULL CHECK (length(trim(requested_by)) > 0),
    risk_level TEXT NOT NULL DEFAULT 'medium' CHECK (
        risk_level IN ('low', 'medium', 'high', 'critical')
    ),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'expired')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    decision_reason TEXT,
    task_id TEXT REFERENCES tasks(id),
    evidence_package JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (
        (decided_at IS NULL AND decided_by IS NULL)
        OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    CHECK (
        status <> 'rejected'
        OR (decision_reason IS NOT NULL
            AND length(trim(decision_reason)) > 0)
    )
);

CREATE INDEX idx_approvals_status ON approvals(status);
CREATE INDEX idx_approvals_action_type ON approvals(action_type);
CREATE INDEX idx_approvals_risk_level ON approvals(risk_level);
CREATE INDEX idx_approvals_requested_by_status ON approvals(requested_by, status);
CREATE INDEX idx_approvals_status_expires_at ON approvals(status, expires_at);
CREATE INDEX idx_approvals_task_id ON approvals(task_id);

-- Org memory: MVCC operation log + materialized snapshot (#1457 A4).
-- Tags are TEXT JSON to match the SQLite backend's serialization;
-- cross-backend parity wins over Postgres-native JSONB idiom here.
CREATE TABLE org_facts_operation_log (
    operation_id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL,
    operation_type TEXT NOT NULL
        CHECK (operation_type IN ('PUBLISH', 'RETRACT')),
    content TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    author_agent_id TEXT,
    author_seniority TEXT,
    author_is_human BOOLEAN NOT NULL DEFAULT FALSE,
    author_autonomy_level TEXT,
    category TEXT,
    timestamp TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE (fact_id, version)
);
CREATE INDEX idx_oplog_fact_id ON org_facts_operation_log (fact_id);
CREATE INDEX idx_oplog_timestamp ON org_facts_operation_log (timestamp);
CREATE INDEX idx_oplog_ts_fact ON org_facts_operation_log (timestamp, fact_id);

CREATE TABLE org_facts_snapshot (
    fact_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    author_agent_id TEXT,
    author_seniority TEXT,
    author_is_human BOOLEAN NOT NULL DEFAULT FALSE,
    author_autonomy_level TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    retracted_at TIMESTAMPTZ,
    version INTEGER NOT NULL
);
CREATE INDEX idx_snapshot_category ON org_facts_snapshot (category);
CREATE INDEX idx_snapshot_active ON org_facts_snapshot (retracted_at)
    WHERE retracted_at IS NULL;

-- Ontology drift reports (#1457 A5).
CREATE TABLE drift_reports (
    id BIGSERIAL PRIMARY KEY,
    entity_name TEXT NOT NULL,
    divergence_score DOUBLE PRECISION NOT NULL,
    canonical_version INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    divergent_agents TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_dr_entity_created
    ON drift_reports (entity_name, created_at DESC);

-- Persistent idempotency keys for retry-prone endpoints (#1599).
-- A claim row goes through (in_flight) -> (completed | failed); the
-- cached response_body lets a duplicate caller receive the same
-- reply rather than 409. Rows older than expires_at are reaped by
-- the periodic cleanup task.
CREATE TABLE idempotency_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('in_flight', 'completed', 'failed')),
    response_hash TEXT,
    response_body JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE INDEX idx_idempotency_expires ON idempotency_keys (expires_at);
