-- SynthOrg SQLite schema -- single source of truth for the sqlite backend.
--
-- This file defines the desired database state for SQLite. Atlas diffs it
-- against the current DB to generate versioned migrations.
-- Do NOT execute this file directly -- use `atlas migrate diff --env sqlite`.
--
-- This is the SQLite-native sibling of src/synthorg/persistence/postgres/schema.sql.
-- Both schemas describe the same logical data model but use each engine's
-- native types.  The conformance suite at tests/conformance/persistence/
-- exercises every repository against both backends so the divergences
-- below are kept honest by parametrized tests, not just by review.
--
-- SQLite-side encoding for fields that Postgres stores natively:
--   * TEXT carrying ``json.dumps(...)`` for fields that Postgres
--     stores as JSONB.  Affected columns:
--       workflow_definitions.{inputs, outputs, nodes, edges},
--       workflow_definition_versions.snapshot,
--       custom_presets.config_json,
--       fine_tune_runs.{config_json, stages_completed},
--       fine_tune_checkpoints.{eval_metrics_json, backup_config_json},
--       agent_identity_versions.snapshot,
--       audit_entries.matched_rules,
--       messages.{attachments, metadata},
--       lifecycle_events.metadata,
--       parked_contexts.{context_json, metadata},
--       tasks.{task_structure, reviewers, dependencies,
--              artifacts_expected, acceptance_criteria,
--              delegation_chain},
--       training_plans.{enabled_content_types, volume_caps,
--                       override_sources},
--       training_results.{source_agents_used, items_extracted,
--                         items_after_curation, items_after_guards,
--                         items_stored, pending_approvals, errors},
--       users.{org_roles, scoped_departments},
--       custom_rules.target_altitudes,
--       conflict_escalations.conflict_json,
--       (and any future JSONB column added to the Postgres schema).
--   * TEXT carrying ISO-8601 strings (with explicit ``+00:00`` or ``Z``
--     suffix; CHECK constraints enforce the suffix on version-snapshot
--     timestamps) for fields that Postgres stores as TIMESTAMPTZ.
--     Repositories normalise to UTC at write time so lexicographic
--     ordering matches chronological ordering.
--   * INTEGER 0/1 for fields that Postgres stores as BOOLEAN
--     (e.g. is_subworkflow, is_active, must_change_password, revoked,
--      health_check_enabled, skip_training, require_review,
--      review_pending, loop_triggered).
--   * INTEGER for fields that Postgres stores as BIGINT;
--     REAL for DOUBLE PRECISION.
--   * No native equivalent for the GIN indexes Postgres builds over
--     JSONB columns (audit_entries.matched_rules,
--      messages.metadata, lifecycle_events.metadata,
--      conflict_escalations.conflict_json).  SQLite falls back to a
--     full-table scan for JSON containment queries; the Postgres
--     ``query_jsonb_contains`` capability protocol is intentionally
--     unimplemented on SQLite repositories.
--
-- Repositories at the Python level return identical Pydantic models
-- from both backends; only the wire serialisation differs.

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
    budget_limit REAL NOT NULL DEFAULT 0.0,
    deadline TEXT,
    max_retries INTEGER NOT NULL DEFAULT 1,
    parent_task_id TEXT,
    task_structure TEXT,
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    reviewers TEXT NOT NULL DEFAULT '[]',
    dependencies TEXT NOT NULL DEFAULT '[]',
    artifacts_expected TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    delegation_chain TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_assigned_to ON tasks(assigned_to);
CREATE INDEX idx_tasks_project ON tasks(project);

-- ── Cost records ──────────────────────────────────────────────
CREATE TABLE cost_records (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
        CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    timestamp TEXT NOT NULL,
    call_category TEXT
);

CREATE INDEX idx_cost_records_agent_id ON cost_records(agent_id);
CREATE INDEX idx_cost_records_task_id ON cost_records(task_id);
CREATE INDEX idx_cost_records_timestamp ON cost_records(timestamp DESC);

-- ── Messages ──────────────────────────────────────────────────
CREATE TABLE messages (
    id TEXT NOT NULL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    sender TEXT NOT NULL,
    "to" TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    channel TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_messages_channel ON messages(channel);
CREATE INDEX idx_messages_timestamp ON messages(timestamp);
CREATE INDEX idx_messages_sender ON messages(sender);
CREATE INDEX idx_messages_to ON messages("to");

-- ── Lifecycle events ──────────────────────────────────────────
CREATE TABLE lifecycle_events (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    initiated_by TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_le_agent_id ON lifecycle_events(agent_id);
CREATE INDEX idx_le_event_type ON lifecycle_events(event_type);
CREATE INDEX idx_le_timestamp ON lifecycle_events(timestamp);

-- ── Task metrics ──────────────────────────────────────────────
CREATE TABLE task_metrics (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    task_type TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    is_success INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    cost REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
        CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    turns_used INTEGER NOT NULL,
    tokens_used INTEGER NOT NULL,
    quality_score REAL,
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
    recorded_at TEXT NOT NULL,
    delegation_success INTEGER,
    delegation_response_seconds REAL,
    conflict_constructiveness REAL,
    meeting_contribution REAL,
    loop_triggered INTEGER NOT NULL DEFAULT 0,
    handoff_completeness REAL
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
    parked_at TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_pc_agent_id ON parked_contexts(agent_id);
CREATE INDEX idx_pc_approval_id ON parked_contexts(approval_id);

-- ── Audit entries ─────────────────────────────────────────────
CREATE TABLE audit_entries (
    id TEXT NOT NULL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    agent_id TEXT,
    task_id TEXT,
    tool_name TEXT NOT NULL,
    tool_category TEXT NOT NULL,
    action_type TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    verdict TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    matched_rules TEXT NOT NULL DEFAULT '[]',
    evaluation_duration_ms REAL NOT NULL,
    approval_id TEXT
);

CREATE INDEX idx_ae_timestamp ON audit_entries(timestamp);
CREATE INDEX idx_ae_agent_id ON audit_entries(agent_id);
CREATE INDEX idx_ae_action_type ON audit_entries(action_type);
CREATE INDEX idx_ae_verdict ON audit_entries(verdict);
CREATE INDEX idx_ae_risk_level ON audit_entries(risk_level);

-- ── Settings (namespaced key-value) ───────────────────────────
CREATE TABLE settings (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);

-- ── Users ─────────────────────────────────────────────────────
CREATE TABLE users (
    id TEXT NOT NULL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    org_roles TEXT NOT NULL DEFAULT '[]',
    scoped_departments TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_users_role ON users(role);
CREATE UNIQUE INDEX idx_single_ceo ON users(role) WHERE role = 'ceo';

-- Prevent removing the last CEO via role change.
CREATE TRIGGER enforce_ceo_minimum
BEFORE UPDATE OF role ON users
WHEN OLD.role = 'ceo' AND NEW.role != 'ceo'
BEGIN
    SELECT RAISE(ABORT, 'Cannot remove the last CEO')
    WHERE (SELECT COUNT(*) FROM users WHERE role = 'ceo' AND id != OLD.id) = 0;
END;

-- Prevent removing the last owner via org_roles change.
CREATE TRIGGER enforce_owner_minimum
BEFORE UPDATE OF org_roles ON users
WHEN EXISTS (SELECT 1 FROM json_each(OLD.org_roles) WHERE value = 'owner')
  AND NOT EXISTS (SELECT 1 FROM json_each(NEW.org_roles) WHERE value = 'owner')
BEGIN
    SELECT RAISE(ABORT, 'Cannot remove the last owner')
    WHERE (
        SELECT COUNT(*) FROM users u, json_each(u.org_roles) je
        WHERE u.id != OLD.id AND je.value = 'owner'
    ) = 0;
END;

-- Prevent deleting the last CEO.
CREATE TRIGGER enforce_ceo_minimum_delete
BEFORE DELETE ON users
WHEN OLD.role = 'ceo'
BEGIN
    SELECT RAISE(ABORT, 'Cannot remove the last CEO')
    WHERE (SELECT COUNT(*) FROM users WHERE role = 'ceo' AND id != OLD.id) = 0;
END;

-- Prevent deleting the last owner.
CREATE TRIGGER enforce_owner_minimum_delete
BEFORE DELETE ON users
WHEN EXISTS (SELECT 1 FROM json_each(OLD.org_roles) WHERE value = 'owner')
BEGIN
    SELECT RAISE(ABORT, 'Cannot remove the last owner')
    WHERE (
        SELECT COUNT(*) FROM users u, json_each(u.org_roles) je
        WHERE u.id != OLD.id AND je.value = 'owner'
    ) = 0;
END;

-- ── API keys ──────────────────────────────────────────────────
CREATE TABLE api_keys (
    id TEXT NOT NULL PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
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
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
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
    turn_number INTEGER NOT NULL CHECK (turn_number >= 0),
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    last_heartbeat_at TEXT NOT NULL
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
    turn_count INTEGER NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
    accumulated_cost REAL NOT NULL DEFAULT 0.0
        CHECK (accumulated_cost >= 0.0),
    currency TEXT NOT NULL DEFAULT 'USD'
        CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    last_activity_at TEXT NOT NULL,
    started_at TEXT,
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
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL,
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
    team TEXT NOT NULL DEFAULT '[]',
    lead TEXT,
    task_ids TEXT NOT NULL DEFAULT '[]',
    deadline TEXT,
    budget REAL NOT NULL DEFAULT 0.0 CHECK (budget >= 0.0),
    status TEXT NOT NULL DEFAULT 'planning'
);

CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_projects_lead ON projects(lead);

-- ── Project-lifetime cost aggregates ─────────────────────────
CREATE TABLE project_cost_aggregates (
    project_id TEXT NOT NULL PRIMARY KEY CHECK(length(project_id) > 0),
    total_cost REAL NOT NULL DEFAULT 0.0 CHECK(total_cost >= 0.0),
    total_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(total_input_tokens >= 0),
    total_output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(total_output_tokens >= 0),
    record_count INTEGER NOT NULL DEFAULT 0 CHECK(record_count >= 0),
    last_updated TEXT NOT NULL CHECK(
        last_updated LIKE '%+00:00' OR last_updated LIKE '%Z'
    )
);

-- ── Custom personality presets (user-defined) ────────────────
CREATE TABLE custom_presets (
    name TEXT NOT NULL PRIMARY KEY CHECK(length(name) > 0),
    config_json TEXT NOT NULL CHECK(length(config_json) > 0),
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workflow_definitions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(id) > 0),
    name TEXT NOT NULL CHECK(length(name) > 0),
    description TEXT NOT NULL DEFAULT '',
    workflow_type TEXT NOT NULL CHECK(workflow_type IN (
        'sequential_pipeline', 'parallel_execution', 'kanban', 'agile_kanban'
    )),
    version TEXT NOT NULL DEFAULT '1.0.0' CHECK(length(version) > 0),
    inputs TEXT NOT NULL DEFAULT '[]',
    outputs TEXT NOT NULL DEFAULT '[]',
    is_subworkflow INTEGER NOT NULL DEFAULT 0 CHECK(is_subworkflow IN (0, 1)),
    nodes TEXT NOT NULL,
    edges TEXT NOT NULL,
    created_by TEXT NOT NULL CHECK(length(created_by) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1)
);

CREATE INDEX idx_wd_workflow_type
    ON workflow_definitions(workflow_type);

CREATE INDEX idx_wd_updated_at
    ON workflow_definitions(updated_at DESC);

CREATE INDEX idx_wd_is_subworkflow
    ON workflow_definitions(is_subworkflow);

-- ── Subworkflow registry (versioned reusable workflow components) ─

CREATE TABLE subworkflows (
    subworkflow_id TEXT NOT NULL CHECK(length(subworkflow_id) > 0),
    semver TEXT NOT NULL CHECK(length(semver) > 0),
    name TEXT NOT NULL CHECK(length(name) > 0),
    description TEXT NOT NULL DEFAULT '',
    workflow_type TEXT NOT NULL CHECK(workflow_type IN (
        'sequential_pipeline', 'parallel_execution', 'kanban', 'agile_kanban'
    )),
    inputs TEXT NOT NULL DEFAULT '[]',
    outputs TEXT NOT NULL DEFAULT '[]',
    nodes TEXT NOT NULL,
    edges TEXT NOT NULL,
    created_by TEXT NOT NULL CHECK(length(created_by) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
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
    id TEXT PRIMARY KEY NOT NULL CHECK(length(id) > 0),
    definition_id TEXT NOT NULL CHECK(length(definition_id) > 0),
    definition_revision INTEGER NOT NULL CHECK(definition_revision >= 1),
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'running', 'completed', 'failed', 'cancelled'
    )),
    node_executions TEXT NOT NULL DEFAULT '[]',
    activated_by TEXT NOT NULL CHECK(length(activated_by) > 0),
    project TEXT NOT NULL CHECK(length(project) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
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

CREATE INDEX idx_wfe_definition_revision
    ON workflow_executions(definition_id, definition_revision);

CREATE INDEX idx_wfe_status_updated
    ON workflow_executions(status, updated_at DESC);

CREATE INDEX idx_wfe_project
    ON workflow_executions(project);

-- ── Fine-tuning pipeline runs ───────────────────────────────────
CREATE TABLE fine_tune_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(id) > 0),
    stage TEXT NOT NULL CHECK(stage IN ('idle', 'generating_data', 'mining_negatives', 'training', 'evaluating', 'deploying', 'complete', 'failed')),
    progress REAL CHECK(progress IS NULL OR (progress >= 0.0 AND progress <= 1.0)),
    error TEXT,
    config_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    stages_completed TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX idx_ftr_stage
    ON fine_tune_runs(stage);

CREATE INDEX idx_ftr_started_at
    ON fine_tune_runs(started_at DESC);

CREATE INDEX idx_ftr_updated_at
    ON fine_tune_runs(updated_at DESC);

-- ── Fine-tuning checkpoints ─────────────────────────────────────
CREATE TABLE fine_tune_checkpoints (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(id) > 0),
    run_id TEXT NOT NULL REFERENCES fine_tune_runs(id) ON DELETE CASCADE,
    model_path TEXT NOT NULL,
    base_model TEXT NOT NULL,
    doc_count INTEGER NOT NULL CHECK(doc_count >= 0),
    eval_metrics_json TEXT,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
    backup_config_json TEXT
);

CREATE INDEX idx_ftc_run_id
    ON fine_tune_checkpoints(run_id);

CREATE INDEX idx_ftc_active
    ON fine_tune_checkpoints(is_active);

CREATE UNIQUE INDEX idx_ftc_single_active
    ON fine_tune_checkpoints(is_active)
    WHERE is_active = 1;

CREATE INDEX idx_ftc_created_at
    ON fine_tune_checkpoints(created_at DESC);

-- ── Workflow Definition Versions ─────────────────────────────

CREATE TABLE workflow_definition_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
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
    reviewer_agent_id TEXT NOT NULL CHECK(reviewer_agent_id != executing_agent_id),
    decision TEXT NOT NULL CHECK(decision IN (
        'approved', 'rejected', 'auto_approved', 'auto_rejected', 'escalated'
    )),
    reason TEXT,
    criteria_snapshot TEXT NOT NULL DEFAULT '[]',
    recorded_at TEXT NOT NULL CHECK(
        recorded_at LIKE '%+00:00' OR recorded_at LIKE '%Z'
    ),
    version INTEGER NOT NULL CHECK(version >= 1),
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(task_id, version)
);

CREATE INDEX idx_dr_executing_agent_recorded
    ON decision_records(executing_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_reviewer_agent_recorded
    ON decision_records(reviewer_agent_id, recorded_at DESC);

-- ── Login Attempts (account lockout) ─────────────────────────
CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
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
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0, 1)),
    created_at TEXT NOT NULL
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
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
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
    timestamp TEXT NOT NULL,
    url TEXT NOT NULL,
    hostname TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    resolved_ip TEXT,
    blocked_range TEXT,
    provider_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'allowed', 'denied')),
    resolved_by TEXT,
    resolved_at TEXT,
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
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_aiv_entity_saved
    ON agent_identity_versions(entity_id, saved_at DESC);
CREATE INDEX idx_aiv_content_hash
    ON agent_identity_versions(entity_id, content_hash);

-- ── Evaluation config versions ────────────────────────────────────

CREATE TABLE evaluation_config_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_ecv_entity_saved
    ON evaluation_config_versions(entity_id, saved_at DESC);
CREATE INDEX idx_ecv_content_hash
    ON evaluation_config_versions(entity_id, content_hash);

-- ── Budget config versions ───────────────────────────────────────

CREATE TABLE budget_config_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_bcv_entity_saved
    ON budget_config_versions(entity_id, saved_at DESC);
CREATE INDEX idx_bcv_content_hash
    ON budget_config_versions(entity_id, content_hash);

-- ── Company versions ─────────────────────────────────────────────

CREATE TABLE company_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_cv_entity_saved
    ON company_versions(entity_id, saved_at DESC);
CREATE INDEX idx_cv_content_hash
    ON company_versions(entity_id, content_hash);

-- ── Role versions ────────────────────────────────────────────────

CREATE TABLE role_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
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
    bounce_count INTEGER NOT NULL DEFAULT 0 CHECK (bounce_count >= 0),
    trip_count INTEGER NOT NULL DEFAULT 0 CHECK (trip_count >= 0),
    opened_at REAL,
    PRIMARY KEY (pair_key_a, pair_key_b)
);

-- ── Ontology: Entity definitions ──────────────────────────────

CREATE TABLE entity_definitions (
    name TEXT NOT NULL PRIMARY KEY CHECK(length(name) > 0),
    tier TEXT NOT NULL CHECK(tier IN ('core', 'user')),
    source TEXT NOT NULL CHECK(source IN ('auto', 'config', 'api')),
    definition TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '[]',
    constraints TEXT NOT NULL DEFAULT '[]',
    disambiguation TEXT NOT NULL DEFAULT '',
    relationships TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL CHECK(length(created_by) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_ed_tier
    ON entity_definitions(tier);

-- ── Ontology: Entity definition versions ──────────────────────

CREATE TABLE entity_definition_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_edv_entity_saved
    ON entity_definition_versions(entity_id, saved_at DESC);
CREATE INDEX idx_edv_content_hash
    ON entity_definition_versions(entity_id, content_hash);

-- ── Connection secrets ───────────────────────────────────────
CREATE TABLE connection_secrets (
    secret_id TEXT NOT NULL PRIMARY KEY CHECK(length(secret_id) > 0),
    encrypted_value BLOB NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1 CHECK(key_version >= 1),
    created_at TEXT NOT NULL,
    rotated_at TEXT
);

-- ── Connections ──────────────────────────────────────────────
CREATE TABLE connections (
    name TEXT NOT NULL PRIMARY KEY CHECK(length(name) > 0),
    connection_type TEXT NOT NULL CHECK(
        connection_type IN (
            'github', 'slack', 'smtp', 'database',
            'generic_http', 'oauth_app'
        )
    ),
    auth_method TEXT NOT NULL CHECK(
        auth_method IN (
            'api_key', 'oauth2', 'basic_auth',
            'bearer_token', 'custom'
        )
    ),
    base_url TEXT,
    secret_refs_json TEXT NOT NULL DEFAULT '[]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0 CHECK(rate_limit_rpm >= 0),
    rate_limit_concurrent INTEGER NOT NULL DEFAULT 0
        CHECK(rate_limit_concurrent >= 0),
    health_check_enabled INTEGER NOT NULL DEFAULT 1
        CHECK(health_check_enabled IN (0, 1)),
    health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(
            health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')
        ),
    last_health_check_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_connections_type ON connections(connection_type);

-- ── OAuth states ─────────────────────────────────────────────
CREATE TABLE oauth_states (
    state_token TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections(name) ON DELETE CASCADE,
    pkce_verifier TEXT,
    scopes_requested TEXT NOT NULL DEFAULT '',
    redirect_uri TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX idx_oauth_states_expires ON oauth_states(expires_at);
CREATE INDEX idx_oauth_states_connection ON oauth_states(connection_name);

-- ── Webhook receipts ─────────────────────────────────────────
CREATE TABLE webhook_receipts (
    id TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections(name) ON DELETE CASCADE,
    event_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'received',
    received_at TEXT NOT NULL,
    processed_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE INDEX idx_webhook_receipts_conn_received
    ON webhook_receipts(connection_name, received_at DESC);

-- ── MCP catalog installations ────────────────────────────────
-- Recorded when the dashboard installs an MCP catalog entry. The
-- MCP bridge merges these rows with the YAML-configured servers at
-- startup (see synthorg.integrations.mcp_catalog.install.
-- merge_installed_servers) so installs survive restarts without
-- touching the user-owned config file.
CREATE TABLE mcp_installations (
    catalog_entry_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(catalog_entry_id) > 0),
    connection_name TEXT REFERENCES connections(name) ON DELETE SET NULL,
    installed_at TEXT NOT NULL
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
    enabled_content_types TEXT NOT NULL DEFAULT '[]',
    curation_strategy_type TEXT NOT NULL DEFAULT 'relevance',
    volume_caps TEXT NOT NULL DEFAULT '[]',
    override_sources TEXT NOT NULL DEFAULT '[]',
    skip_training INTEGER NOT NULL DEFAULT 0,
    require_review INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'executed', 'failed')),
    created_at TEXT NOT NULL,
    executed_at TEXT,
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
    source_agents_used TEXT NOT NULL DEFAULT '[]',
    items_extracted TEXT NOT NULL DEFAULT '[]',
    items_after_curation TEXT NOT NULL DEFAULT '[]',
    items_after_guards TEXT NOT NULL DEFAULT '[]',
    items_stored TEXT NOT NULL DEFAULT '[]',
    approval_item_id TEXT,
    pending_approvals TEXT NOT NULL DEFAULT '[]',
    review_pending INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    CHECK(completed_at >= started_at)
);

CREATE UNIQUE INDEX idx_training_results_plan
    ON training_results(plan_id);
CREATE INDEX idx_training_results_agent
    ON training_results(new_agent_id, completed_at DESC);

-- ── Custom signal rules ─────────────────────────────────────────

CREATE TABLE custom_rules (
    id TEXT NOT NULL PRIMARY KEY CHECK(length(id) > 0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    description TEXT NOT NULL CHECK(length(trim(description)) > 0),
    metric_path TEXT NOT NULL CHECK(length(trim(metric_path)) > 0),
    comparator TEXT NOT NULL CHECK(length(trim(comparator)) > 0),
    threshold REAL NOT NULL,
    severity TEXT NOT NULL CHECK(length(trim(severity)) > 0),
    target_altitudes TEXT NOT NULL,  -- JSON array of altitude strings
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    updated_at TEXT NOT NULL CHECK(
        updated_at LIKE '%+00:00' OR updated_at LIKE '%Z'
    )
);
CREATE UNIQUE INDEX custom_rules_name ON custom_rules (name);
CREATE INDEX idx_custom_rules_enabled ON custom_rules(enabled);

-- Approvals (meta-loop human review queue)
CREATE TABLE approvals (
    id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(id)) > 0),
    action_type TEXT NOT NULL CHECK(length(trim(action_type)) > 0),
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    description TEXT NOT NULL,
    requested_by TEXT NOT NULL CHECK(length(trim(requested_by)) > 0),
    risk_level TEXT NOT NULL DEFAULT 'medium' CHECK(
        risk_level IN ('low', 'medium', 'high', 'critical')
    ),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(
        status IN ('pending', 'approved', 'rejected', 'expired')
    ),
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    expires_at TEXT CHECK(
        expires_at IS NULL OR expires_at LIKE '%+00:00' OR expires_at LIKE '%Z'
    ),
    decided_at TEXT CHECK(
        decided_at IS NULL OR decided_at LIKE '%+00:00' OR decided_at LIKE '%Z'
    ),
    decided_by TEXT,
    decision_reason TEXT,
    task_id TEXT CONSTRAINT fk_approvals_task_id REFERENCES tasks(id),
    evidence_package TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    CHECK(
        (decided_at IS NULL AND decided_by IS NULL)
        OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    CHECK(
        status != 'rejected' OR (decision_reason IS NOT NULL AND length(trim(decision_reason)) > 0)
    )
);
CREATE INDEX idx_approvals_status ON approvals(status);
CREATE INDEX idx_approvals_action_type ON approvals(action_type);
CREATE INDEX idx_approvals_risk_level ON approvals(risk_level);
CREATE INDEX idx_approvals_requested_by_status ON approvals(requested_by, status);
CREATE INDEX idx_approvals_status_expires_at ON approvals(status, expires_at);
CREATE INDEX idx_approvals_task_id ON approvals(task_id);

-- Conflict escalations (#1418: human escalation approval queue).
-- Persists one row per conflict awaiting a human decision so the
-- queue survives process restarts and auditors can replay decisions.
CREATE TABLE conflict_escalations (
    id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(id)) > 0),
    conflict_id TEXT NOT NULL CHECK(length(trim(conflict_id)) > 0),
    conflict_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(
        status IN ('pending', 'decided', 'expired', 'cancelled')
    ),
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    expires_at TEXT CHECK(
        expires_at IS NULL OR expires_at LIKE '%+00:00' OR expires_at LIKE '%Z'
    ),
    decided_at TEXT CHECK(
        decided_at IS NULL OR decided_at LIKE '%+00:00' OR decided_at LIKE '%Z'
    ),
    decided_by TEXT,
    decision_json TEXT,
    -- Payload columns must hold valid JSON objects (not scalars,
    -- arrays, or nulls) so a corrupt write (e.g., from an
    -- out-of-band migration or a faulty ETL) cannot persist shapes
    -- the repository cannot deserialize.  ``json_valid`` and
    -- ``json_type`` are SQLite core functions (3.38+).
    CHECK(json_valid(conflict_json) AND json_type(conflict_json) = 'object'),
    CHECK(
        decision_json IS NULL
        OR (json_valid(decision_json) AND json_type(decision_json) = 'object')
    ),
    -- DECIDED rows carry the full decision triple; decided_by must
    -- be a nonblank actor identifier so audit consumers can always
    -- attribute the transition.
    CHECK(
        (status != 'decided')
        OR (
            decision_json IS NOT NULL
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND length(trim(decided_by)) > 0
        )
    ),
    -- PENDING rows carry no decision triple at all (decided_by must
    -- also be NULL so audit consumers can distinguish pending from
    -- terminal states by column nullability alone).
    CHECK(
        (status != 'pending')
        OR (decision_json IS NULL AND decided_at IS NULL AND decided_by IS NULL)
    ),
    -- EXPIRED / CANCELLED rows drop any decision payload but MUST
    -- carry both audit-trail columns (transition timestamp +
    -- attributable nonblank actor "system:..." or "human:...") so
    -- auditors can always answer "who expired/cancelled this, and when".
    CHECK(
        (status NOT IN ('expired', 'cancelled'))
        OR (
            decision_json IS NULL
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND length(trim(decided_by)) > 0
        )
    )
);
CREATE INDEX idx_conflict_escalations_status_created ON
    conflict_escalations(status, created_at);
CREATE INDEX idx_conflict_escalations_conflict_id ON
    conflict_escalations(conflict_id);
CREATE INDEX idx_conflict_escalations_status_expires_at ON
    conflict_escalations(status, expires_at);
-- Enforce "at most one PENDING escalation per conflict" so two
-- concurrent resolvers cannot enqueue competing queue rows for the
-- same conflict.
CREATE UNIQUE INDEX idx_conflict_escalations_unique_pending_conflict ON
    conflict_escalations(conflict_id) WHERE status = 'pending';

-- Org memory: MVCC operation log + materialized snapshot (#1457 A4).
CREATE TABLE org_facts_operation_log (
    operation_id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK(operation_type IN ('PUBLISH', 'RETRACT')),
    content TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    author_agent_id TEXT,
    author_seniority TEXT,
    author_is_human INTEGER NOT NULL DEFAULT 0,
    author_autonomy_level TEXT,
    category TEXT,
    timestamp TEXT NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE(fact_id, version)
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
    author_is_human INTEGER NOT NULL DEFAULT 0,
    author_autonomy_level TEXT,
    created_at TEXT NOT NULL,
    retracted_at TEXT,
    version INTEGER NOT NULL
);
CREATE INDEX idx_snapshot_category ON org_facts_snapshot (category);
CREATE INDEX idx_snapshot_active ON org_facts_snapshot (retracted_at)
    WHERE retracted_at IS NULL;

-- Ontology drift reports (#1457 A5).
CREATE TABLE drift_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name TEXT NOT NULL,
    divergence_score REAL NOT NULL,
    canonical_version INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    divergent_agents TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_dr_entity_created
    ON drift_reports(entity_name, created_at DESC);

-- Persistent idempotency keys for retry-prone endpoints (#1599).
-- A claim row goes through (in_flight) -> (completed | failed); the
-- cached response_body lets a duplicate caller receive the same
-- reply rather than 409. Rows older than expires_at are reaped by
-- the periodic cleanup task.
CREATE TABLE idempotency_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('in_flight', 'completed', 'failed')),
    response_hash TEXT,
    response_body TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);
