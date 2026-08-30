-- SynthOrg Postgres schema -- single source of truth for the postgres backend.
--
-- This file defines the desired database state for Postgres.  The drift
-- gate (`scripts/check_schema_drift_revisions.py --backend postgres`)
-- diffs this against the accumulated revisions in `revisions/` and
-- fails CI on mismatch.  The runtime applies the schema through yoyo;
-- the `revisions/00000000000000_baseline.sql` seed is a verbatim copy
-- of this declared schema.
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
--      lifecycle_events.metadata).
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
    plan_id TEXT,
    plan_item_id TEXT,
    created_by TEXT NOT NULL,
    requested_by_user_id TEXT,
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    estimated_complexity TEXT NOT NULL DEFAULT 'medium',
    budget_limit DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    deadline TIMESTAMPTZ,
    max_retries BIGINT NOT NULL DEFAULT 1,
    parent_task_id TEXT,
    task_structure JSONB,
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    reviewers JSONB NOT NULL DEFAULT '[]'::JSONB,
    dependencies JSONB NOT NULL DEFAULT '[]'::JSONB,
    artifacts_expected JSONB NOT NULL DEFAULT '[]'::JSONB,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]'::JSONB,
    delegation_chain JSONB NOT NULL DEFAULT '[]'::JSONB,
    hard_ceiling DOUBLE PRECISION,
    forecast_id TEXT,
    source TEXT,
    middleware_override JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    -- The money ceiling above cannot fire against a provider that bills by
    -- flat subscription, because cost never rises. Tokens are measured on
    -- every provider, billed or not, so this is the same backstop in the
    -- unit that is always available. NULL falls back to the global
    -- budget.run_hard_token_ceiling setting, matching hard_ceiling.
    hard_token_ceiling BIGINT CHECK (hard_token_ceiling >= 0),
    -- Why the task is parked at BLOCKED, when the writer named it. BLOCKED is
    -- reached from several directions, so a rule written for one of them reads
    -- this rather than the status. NULL means unnamed, never a member.
    blocked_reason TEXT CONSTRAINT tasks_blocked_reason_check CHECK (
        blocked_reason IN (
            'oracle_escalated',
            'wave_released',
            'reviewer_unstaffed',
            'red_team_unstaffed',
            'no_capable_agent',
            'dependency_failed',
            'run_stopped'
        )
    ),
    -- When the task was filed. No DB default: the application is the single
    -- owner of the value, so a row cannot acquire a timestamp from the
    -- database clock that disagrees with the one the model carries.
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_tasks_status ON tasks (status);
CREATE INDEX idx_tasks_assigned_to ON tasks (assigned_to);
CREATE INDEX idx_tasks_project ON tasks (project);
CREATE INDEX idx_tasks_plan_id ON tasks (plan_id);
-- Serves the unroutable sweep, which pages parked tasks by reason every pass.
-- ``id`` trails so that keyset walk is one index range scan.
CREATE INDEX idx_tasks_status_blocked_reason ON tasks (status, blocked_reason, id);

-- ── Cost records ──────────────────────────────────────────────
-- Composite (rowid, timestamp) primary key: TimescaleDB hypertables
-- require the partitioning column to appear in every unique index.
-- On deployments where TimescaleDB is available, the Postgres backend
-- converts this table to a hypertable at runtime during its
-- post-migration setup step; on vanilla Postgres the composite PK is
-- functionally equivalent to the old ``PRIMARY KEY (rowid)`` because
-- ``rowid`` is still globally unique via the IDENTITY sequence.
CREATE TABLE cost_records (
    rowid BIGINT GENERATED ALWAYS AS IDENTITY,
    -- Nullable because subsystem work (embedding, reranking, consolidation,
    -- safety classification) belongs to no agent and no task. task_id is a
    -- real foreign key, so inventing an id for those calls made every one of
    -- their inserts fail the constraint and lose the spend; what the call was
    -- for is carried by prompt_class_id instead.
    agent_id TEXT,
    -- No foreign key, for the same reason project_id below carries none: a
    -- cost row is evidence of a call that really happened, and the pin made
    -- it a veto on ever removing the task. A live run could not delete a
    -- project because one of its tasks had spent money, and the refusal
    -- read as a constraint name rather than a reason. The identifier is
    -- retained verbatim, and ``deleted_entities`` says what it was.
    task_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens BIGINT NOT NULL,
    output_tokens BIGINT NOT NULL,
    cost DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency ~ '^[A-Z]{3}$'),
    timestamp TIMESTAMPTZ NOT NULL,
    call_category TEXT,
    prompt_class_id TEXT,
    -- The tracker's idempotency key, persisted so the durable append is
    -- idempotent for a record with no project (the project-aggregate path
    -- skips dedup entirely for those).
    claim_id TEXT,
    -- No foreign key, matching project_cost_aggregates.project_id, which the
    -- same record() call writes in the same transaction. A cost row is
    -- evidence of a call that really happened: refusing the insert because
    -- the project row is missing would lose the spend rather than protect
    -- it, and would leave the aggregate counting money this table dropped.
    project_id TEXT,
    -- How the provider charged, stamped from the connection's own
    -- declaration at ingestion. Carried on the row for the same reason
    -- currency is: a connection that later changes contract must not
    -- rewrite the history of what was measurable, and one since deleted
    -- must still be answerable. Without it a cost of 0.0 says two
    -- different things and only one of them is headroom.
    billing_model TEXT NOT NULL DEFAULT 'unknown'
    CHECK (billing_model IN ('per_token', 'flat_rate', 'unknown')),
    PRIMARY KEY (rowid, timestamp)
);

CREATE INDEX idx_cost_records_agent_id ON cost_records (agent_id);
CREATE INDEX idx_cost_records_task_id ON cost_records (task_id);
CREATE INDEX idx_cost_records_timestamp ON cost_records (timestamp DESC);
CREATE INDEX idx_cost_records_agent_timestamp
ON cost_records (agent_id, timestamp DESC);
CREATE INDEX idx_cost_records_task_timestamp
ON cost_records (task_id, timestamp DESC);
CREATE INDEX idx_cost_records_prompt_class_timestamp
ON cost_records (prompt_class_id, timestamp DESC);
-- ``timestamp`` rides in the key because this table becomes a TimescaleDB
-- hypertable at connect time, where a unique index must include the
-- partitioning column. It still catches the duplicate that happens: a
-- redelivery carries the same record.
CREATE UNIQUE INDEX idx_cost_records_claim_id
ON cost_records (claim_id, timestamp);
CREATE INDEX idx_cost_records_project_timestamp
ON cost_records (project_id, timestamp DESC);

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
    attachments JSONB NOT NULL DEFAULT '[]'::JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX idx_messages_channel ON messages (channel);
CREATE INDEX idx_messages_timestamp ON messages (timestamp);
CREATE INDEX idx_messages_metadata_gin ON messages USING GIN (metadata);
CREATE INDEX idx_messages_sender ON messages (sender);
CREATE INDEX idx_messages_to ON messages ("to");

-- ── Lifecycle events ──────────────────────────────────────────
CREATE TABLE lifecycle_events (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    initiated_by TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX idx_le_agent_id ON lifecycle_events (agent_id);
CREATE INDEX idx_le_event_type ON lifecycle_events (event_type);
CREATE INDEX idx_le_timestamp ON lifecycle_events (timestamp);
CREATE INDEX idx_le_metadata_gin ON lifecycle_events USING GIN (metadata);

-- ── Task metrics ──────────────────────────────────────────────
CREATE TABLE task_metrics (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    -- Unpinned like cost_records.task_id: a measurement of a run that
    -- happened must not be able to veto removing what it measured.
    task_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    is_success BOOLEAN NOT NULL,
    duration_seconds DOUBLE PRECISION,
    cost DOUBLE PRECISION,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency ~ '^[A-Z]{3}$'),
    turns_used BIGINT,
    tokens_used BIGINT,
    quality_score DOUBLE PRECISION,
    complexity TEXT NOT NULL,
    run_outcome TEXT
    CHECK (run_outcome IN ('succeeded', 'empty', 'failed'))
);

CREATE INDEX idx_tm_agent_id ON task_metrics (agent_id);
CREATE INDEX idx_tm_completed_at ON task_metrics (completed_at);
CREATE INDEX idx_tm_agent_completed
ON task_metrics (agent_id, completed_at);
-- A referencing column with no index makes every delete of the referenced
-- row a full scan of this table.
CREATE INDEX idx_tm_task_id ON task_metrics (task_id);

-- ── Parked contexts ───────────────────────────────────────────
CREATE TABLE parked_contexts (
    id TEXT NOT NULL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT,
    approval_id TEXT NOT NULL,
    parked_at TIMESTAMPTZ NOT NULL,
    context_json JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX idx_pc_agent_id ON parked_contexts (agent_id);
CREATE INDEX idx_pc_approval_id ON parked_contexts (approval_id);
-- Composite index for "list parked contexts for agent X newest-first".
-- Mirrors the SQLite shape so cursor pagination is driven by an
-- indexed (agent_id, parked_at DESC) seek on both backends.
CREATE INDEX idx_parked_contexts_agent_parked_at
ON parked_contexts (agent_id, parked_at DESC);

-- ── Resume intents ────────────────────────────────────────────
-- Crash-recovery marker for the two-write approval decision: written
-- before the decision lands on the approval and cleared once the resume
-- has dispatched, so a row surviving a restart means "this approval's
-- parked task may never have been woken". Keyed by approval_id (one
-- in-flight resume per approval); the decision itself is NOT copied
-- here, the approval row stays the system of record.
CREATE TABLE resume_intents (
    approval_id TEXT NOT NULL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL
);

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
    matched_rules JSONB NOT NULL DEFAULT '[]'::JSONB,
    evaluation_duration_ms DOUBLE PRECISION NOT NULL,
    approval_id TEXT,
    PRIMARY KEY (id, timestamp)
);

CREATE INDEX idx_ae_timestamp ON audit_entries (timestamp);
CREATE INDEX idx_ae_agent_id ON audit_entries (agent_id);
CREATE INDEX idx_ae_action_type ON audit_entries (action_type);
CREATE INDEX idx_ae_verdict ON audit_entries (verdict);
CREATE INDEX idx_ae_risk_level ON audit_entries (risk_level);
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
    org_roles JSONB NOT NULL DEFAULT '[]'::JSONB,
    scoped_departments JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_users_role ON users (role);
CREATE UNIQUE INDEX idx_single_ceo ON users (role) WHERE role = 'ceo';

-- Prevent removing the last CEO via role change.
--
-- Takes a transaction-scoped advisory lock so two concurrent
-- transactions cannot both see each other's pre-commit CEO row
-- as still-present.  The lock id (42_001) is arbitrary but stable;
-- the sibling enforce_owner_minimum uses a disjoint id so the two
-- triggers never contend with each other.
-- Function and trigger bodies are exempt from sqlfluff: they must byte-match
-- the frozen revision DDL that check_schema_drift_revisions.py compares
-- case-sensitively, so layout / capitalisation auto-fixes must not rewrite them.
-- noqa: disable=all
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
-- noqa: enable=all

-- ── API keys ──────────────────────────────────────────────────
CREATE TABLE api_keys (
    id TEXT NOT NULL PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_api_keys_user_id ON api_keys (user_id);
-- Composite index for "list api_keys for user X with stable ordering".
-- The trailing ``id`` keeps cursor pagination stable across rows that
-- share a ``created_at`` timestamp.
CREATE INDEX idx_api_keys_user_created_id
ON api_keys (user_id, created_at, id);

-- ── Sessions ─────────────────────────────────────────────────
CREATE TABLE sessions (
    session_id TEXT NOT NULL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
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
ON sessions (user_id, revoked, expires_at);
CREATE INDEX idx_sessions_revoked_expires
ON sessions (revoked, expires_at);
CREATE INDEX idx_sessions_expires_at ON sessions (expires_at);
CREATE INDEX idx_sessions_user_created
ON sessions (user_id, revoked, created_at DESC, session_id ASC);

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

CREATE INDEX idx_cp_execution_id ON checkpoints (execution_id);
CREATE INDEX idx_cp_task_id ON checkpoints (task_id);
CREATE INDEX idx_cp_exec_turn
ON checkpoints (execution_id, turn_number);
CREATE INDEX idx_cp_task_turn
ON checkpoints (task_id, turn_number);

-- ── Flight-recorder frames ────────────────────────────────────
CREATE TABLE flight_recorder_frames (
    id TEXT NOT NULL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    task_id TEXT,
    agent_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL CHECK (turn_index >= 1),
    timestamp TIMESTAMPTZ NOT NULL,
    prompt_summary TEXT,
    response_summary TEXT,
    decision TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]'::JSONB,
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cost DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (cost >= 0),
    status TEXT NOT NULL,
    intervention_kind TEXT
);

CREATE UNIQUE INDEX idx_frf_execution_turn
ON flight_recorder_frames (execution_id, turn_index);
CREATE INDEX idx_frf_task_id ON flight_recorder_frames (task_id);
CREATE INDEX idx_frf_agent_id ON flight_recorder_frames (agent_id);
CREATE INDEX idx_frf_timestamp ON flight_recorder_frames (timestamp);

-- ── Red-team report archive ───────────────────────────────────
-- Durable audit record of one red-team gate evaluation. Identity is the
-- surrogate ``report_id``, not ``execution_id``: the gate runs again whenever
-- a task is decided, re-opened and decided again, so a report is one review
-- event rather than one execution. The merged report is stored as JSON text in
-- ``report_json``; ``task_id`` / ``verdict`` / ``finding_count`` /
-- ``report_summary`` are structured columns the flight-recorder read surface
-- filters and previews on, and the red-teamer / executor / model columns make
-- verdict quality comparable per agent and per model. The party columns are
-- nullable only because rows written before the red-teamer was a roster agent
-- cannot name either party; the CHECK guards every row that names both.
CREATE TABLE red_team_reports (
    report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    red_team_agent_id TEXT,
    executor_agent_id TEXT,
    red_team_provider TEXT,
    red_team_model_id TEXT,
    red_team_capability TEXT CHECK (
        red_team_capability IS NULL
        OR red_team_capability IN ('basic', 'capable', 'expert')
    ),
    verdict TEXT NOT NULL CHECK (
        verdict IN ('pass', 'pass_with_findings', 'block')
    ),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    report_summary TEXT NOT NULL,
    report_json TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT red_team_reports_distinct_parties_check CHECK (
        red_team_agent_id IS NULL
        OR executor_agent_id IS NULL
        OR executor_agent_id != red_team_agent_id
    )
);

CREATE INDEX idx_rtr_task_id ON red_team_reports (task_id, recorded_at DESC);
CREATE INDEX idx_rtr_verdict ON red_team_reports (verdict, recorded_at DESC);
CREATE INDEX idx_rtr_recorded_at ON red_team_reports (recorded_at DESC);
CREATE INDEX idx_rtr_execution_id
ON red_team_reports (execution_id, recorded_at DESC);
CREATE INDEX idx_rtr_red_team_agent_id
ON red_team_reports (red_team_agent_id, recorded_at DESC);

-- Durable audit archive of completion-oracle peer-review verdicts (one row
-- per review event). The full report is JSON in ``report_json``; ``verdict`` /
-- ``reviewer_agent_id`` / ``executor_agent_id`` / ``finding_count`` /
-- ``report_summary`` are structured columns the read surface filters and
-- previews on, and the reviewer model columns record what actually produced
-- the verdict (NULL on rows written before they existed). The row-level CHECK
-- enforces the reviewer-is-distinct invariant for any non-Pydantic writer,
-- mirroring decision_records.
CREATE TABLE completion_oracle_reports (
    report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    reviewer_agent_id TEXT,
    executor_agent_id TEXT,
    reviewer_provider TEXT,
    reviewer_model_id TEXT,
    reviewer_capability TEXT CHECK (
        reviewer_capability IS NULL
        OR reviewer_capability IN ('basic', 'capable', 'expert')
    ),
    verdict TEXT NOT NULL CHECK (
        verdict IN ('approve', 'approve_with_notes', 'reject', 'escalate')
    ),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    report_summary TEXT NOT NULL,
    report_json TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    -- Named table constraint, matching red_team_reports and the revision that
    -- adds it: declared inline on a column, Postgres auto-names it
    -- ``completion_oracle_reports_check``, so a fresh install and a migrated
    -- install would carry one rule under two names and a later revision that
    -- drops it by name would fail on whichever install it did not match.
    CONSTRAINT completion_oracle_reports_distinct_parties_check CHECK (
        reviewer_agent_id IS NULL
        OR executor_agent_id IS NULL
        OR executor_agent_id != reviewer_agent_id
    )
);

CREATE INDEX idx_cor_task_id ON completion_oracle_reports (task_id, recorded_at DESC);
CREATE INDEX idx_cor_verdict ON completion_oracle_reports (verdict, recorded_at DESC);
CREATE INDEX idx_cor_recorded_at ON completion_oracle_reports (recorded_at DESC);
CREATE INDEX idx_cor_execution_id
ON completion_oracle_reports (execution_id, recorded_at DESC);
CREATE INDEX idx_cor_reviewer_agent_id
ON completion_oracle_reports (reviewer_agent_id, recorded_at DESC);

-- ── Heartbeats ────────────────────────────────────────────────
CREATE TABLE heartbeats (
    execution_id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_hb_last_heartbeat
ON heartbeats (last_heartbeat_at, execution_id);

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
        (
            status = 'idle'
            AND execution_id IS NULL
            AND task_id IS NULL
            AND started_at IS NULL
            AND turn_count = 0
            AND accumulated_cost = 0.0
        )
        OR
        (
            status IN ('executing', 'paused')
            AND execution_id IS NOT NULL
            AND started_at IS NOT NULL
        )
    )
);

CREATE INDEX idx_as_status_activity
ON agent_states (status, last_activity_at DESC);

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

CREATE INDEX idx_artifacts_task_id ON artifacts (task_id);
CREATE INDEX idx_artifacts_created_by ON artifacts (created_by);
CREATE INDEX idx_artifacts_type ON artifacts (type);
CREATE INDEX idx_artifacts_project_id ON artifacts (project_id);

-- ── Projects ─────────────────────────────────────────────────
CREATE TABLE projects (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    lead TEXT,
    plan_id TEXT,
    deadline TIMESTAMPTZ,
    budget DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (budget >= 0.0),
    status TEXT NOT NULL DEFAULT 'planning',
    autonomy_mode TEXT CHECK (autonomy_mode IN ('full', 'semi', 'supervised', 'locked')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_projects_status ON projects (status);
CREATE INDEX idx_projects_lead ON projects (lead);
-- Intake looks up a live project by age, so the ordering column is indexed.
CREATE INDEX idx_projects_created_at ON projects (created_at);

-- ── Persistent per-project workspace (1:1 with projects) ─────
CREATE TABLE project_workspaces (
    project_id TEXT NOT NULL PRIMARY KEY,
    workspace_path TEXT NOT NULL UNIQUE,
    git_backend_kind TEXT NOT NULL
    CHECK (git_backend_kind IN ('embedded', 'external_remote', 'local_path')),
    remote_ref TEXT,
    default_branch TEXT NOT NULL DEFAULT 'main',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_project_workspaces_created_at
ON project_workspaces (created_at);

-- ── Persistent per-project reproducible environment (1:1) ────
CREATE TABLE project_environments (
    project_id TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(project_id)) > 0),
    environment_type TEXT NOT NULL
    CHECK (environment_type IN ('manifest', 'devcontainer', 'nix')),
    declaration_hash TEXT NOT NULL
    CHECK (LENGTH(TRIM(declaration_hash)) > 0),
    image_ref TEXT,
    provisioned_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (updated_at >= provisioned_at),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_project_environments_declaration_hash
ON project_environments (declaration_hash);

-- ── Brownfield codebase structure map (1:1 with projects) ────
CREATE TABLE codebase_structure_maps (
    project_id TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(project_id)) > 0),
    source_ref TEXT NOT NULL
    CHECK (LENGTH(TRIM(source_ref)) > 0),
    modules JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(modules) = 'array'),
    entry_points JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(entry_points) = 'array'),
    test_suites JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(test_suites) = 'array'),
    build_files JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(build_files) = 'array'),
    dependencies JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(dependencies) = 'array'),
    scanned_at TIMESTAMPTZ NOT NULL,
    content_hash TEXT NOT NULL
    CHECK (LENGTH(content_hash) = 64),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_codebase_structure_maps_content_hash
ON codebase_structure_maps (content_hash);

-- ── Living-documentation metadata ────────────────────────────
CREATE TABLE project_docs (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    doc_type TEXT NOT NULL
    CONSTRAINT project_docs_doc_type_check CHECK (
        doc_type IN (
            'status_report',
            'deliverable',
            'knowledge_note',
            'codebase_analysis',
            'run_narrative'
        )
    ),
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    related_task_ids TEXT NOT NULL DEFAULT '[]',
    head_commit_sha TEXT NOT NULL,
    last_indexed_commit_sha TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (project_id, slug),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_project_docs_updated_at
ON project_docs (updated_at DESC);

CREATE INDEX idx_project_docs_project_recent
ON project_docs (project_id, updated_at DESC, slug DESC);

CREATE INDEX idx_project_docs_doc_type
ON project_docs (project_id, doc_type);

CREATE INDEX idx_project_docs_reindex
ON project_docs (project_id)
WHERE last_indexed_commit_sha IS NULL
OR last_indexed_commit_sha != head_commit_sha;

-- ── Long-horizon project brain ───────────────────────────────
-- Append-only structured project state: decisions, open
-- questions, blockers, risks, dependencies, plan revisions. A change to
-- a logical entry is a new row (same entry_id, revision incremented);
-- current state is the latest revision per entry_id. payload is a
-- kind-discriminated JSON object; related ids, tags, and citations are
-- JSON arrays. ON DELETE CASCADE drops a project's brain.
CREATE TABLE project_brain_entries (
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
    recorded_at TIMESTAMPTZ NOT NULL,
    related_task_ids JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(related_task_ids) = 'array'),
    related_entry_ids JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(related_entry_ids) = 'array'),
    supersedes_entry_id TEXT,
    tags JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(tags) = 'array'),
    confidence DOUBLE PRECISION,
    citations JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(citations) = 'array'),
    payload JSONB NOT NULL,
    PRIMARY KEY (project_id, entry_id, revision),
    -- Redundant with the PK for per-project lookups, kept deliberately: it
    -- enforces a globally unique (entry_id, revision) pair, so a revision is
    -- addressable across projects without the project_id prefix.
    UNIQUE (entry_id, revision),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

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

-- Tracks the highest brain revision per entry confirmed present in the RAG
-- index. A mutable bookkeeping projection (upsert), distinct from the
-- append-only entries log: the write path persists the SQL row before the
-- best-effort index, so a transient index failure leaves a gap. Boot replay
-- diffs current revision against last_indexed_revision here and re-indexes only
-- the gap, so a never-revised entry whose index write failed still becomes
-- searchable. ON DELETE CASCADE: deleting a project drops its index state.
CREATE TABLE project_brain_index_state (
    project_id TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(project_id)) > 0),
    entry_id TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(entry_id)) > 0),
    last_indexed_revision INTEGER NOT NULL
    CHECK (last_indexed_revision >= 1),
    PRIMARY KEY (project_id, entry_id),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

-- ── Knowledge + provenance substrate ─────────────────────────
-- Registry of ingested corpus sources (PDF / web / repo / ticket /
-- design doc). project_id is nullable: NULL means a global source
-- shared across projects. ON DELETE CASCADE drops a project's scoped
-- sources; global sources survive. Chunk text lives in the memory
-- backend; only provenance lives below.
CREATE TABLE knowledge_sources (
    source_id TEXT NOT NULL PRIMARY KEY,
    source_type TEXT NOT NULL
    CHECK (source_type IN ('pdf', 'web', 'repo', 'ticket', 'design_doc')),
    project_id TEXT,
    uri TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL
    CHECK (status IN ('pending', 'indexed', 'stale', 'failed')),
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    last_indexed_at TIMESTAMPTZ,
    last_error TEXT,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_knowledge_sources_updated_at
ON knowledge_sources (updated_at DESC, source_id DESC);

CREATE INDEX idx_knowledge_sources_project_status
ON knowledge_sources (project_id, status);

CREATE INDEX idx_knowledge_sources_stale
ON knowledge_sources (updated_at DESC)
WHERE status = 'stale';

CREATE INDEX idx_knowledge_sources_global
ON knowledge_sources (updated_at DESC)
WHERE project_id IS NULL;

-- Per-chunk provenance for citation resolution. locator_json stores the
-- serialised ProvenanceLocator discriminated union (locator_kind names
-- the variant). Replaced wholesale on re-index via delete_by_source.
CREATE TABLE knowledge_chunk_provenance (
    chunk_id TEXT NOT NULL PRIMARY KEY,
    source_id TEXT NOT NULL,
    content_kind TEXT NOT NULL
    CHECK (content_kind IN ('code', 'document', 'pdf_page', 'ticket_thread')),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content_hash TEXT NOT NULL,
    locator_kind TEXT NOT NULL
    CHECK (locator_kind IN ('pdf', 'web', 'code', 'ticket')),
    locator_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (source_id) REFERENCES knowledge_sources (source_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_knowledge_provenance_source
ON knowledge_chunk_provenance (source_id, chunk_index);

-- ── Project-lifetime cost aggregates ─────────────────────────
CREATE TABLE project_cost_aggregates (
    project_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(project_id) > 0),
    total_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (total_cost >= 0.0),
    total_input_tokens BIGINT NOT NULL DEFAULT 0
    CHECK (total_input_tokens >= 0),
    total_output_tokens BIGINT NOT NULL DEFAULT 0
    CHECK (total_output_tokens >= 0),
    record_count BIGINT NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    last_updated TIMESTAMPTZ NOT NULL
);

CREATE TABLE workflow_definitions (
    id TEXT PRIMARY KEY NOT NULL CHECK (LENGTH(id) > 0),
    name TEXT NOT NULL CHECK (LENGTH(name) > 0),
    description TEXT NOT NULL DEFAULT '',
    workflow_type TEXT NOT NULL CHECK (workflow_type IN (
        'sequential_pipeline', 'parallel_execution', 'kanban', 'agile_kanban'
    )),
    version TEXT NOT NULL DEFAULT '1.0.0' CHECK (LENGTH(version) > 0),
    inputs JSONB NOT NULL DEFAULT '[]'::JSONB,
    outputs JSONB NOT NULL DEFAULT '[]'::JSONB,
    is_subworkflow BOOLEAN NOT NULL DEFAULT FALSE,
    nodes JSONB NOT NULL,
    edges JSONB NOT NULL,
    created_by TEXT NOT NULL CHECK (LENGTH(created_by) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1)
);

CREATE INDEX idx_wd_workflow_type
ON workflow_definitions (workflow_type);

CREATE INDEX idx_wd_updated_at
ON workflow_definitions (updated_at DESC);

CREATE INDEX idx_wd_is_subworkflow
ON workflow_definitions (is_subworkflow);

-- ── Subworkflows ─────────────────────────────────────────────

CREATE TABLE subworkflows (
    subworkflow_id TEXT NOT NULL CHECK (LENGTH(subworkflow_id) > 0),
    semver TEXT NOT NULL CHECK (LENGTH(semver) > 0),
    name TEXT NOT NULL CHECK (LENGTH(name) > 0),
    description TEXT NOT NULL DEFAULT '',
    workflow_type TEXT NOT NULL CHECK (workflow_type IN (
        'sequential_pipeline', 'parallel_execution', 'kanban', 'agile_kanban'
    )),
    inputs JSONB NOT NULL DEFAULT '[]'::JSONB,
    outputs JSONB NOT NULL DEFAULT '[]'::JSONB,
    nodes JSONB NOT NULL,
    edges JSONB NOT NULL,
    created_by TEXT NOT NULL CHECK (LENGTH(created_by) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'::TIMESTAMPTZ,
    PRIMARY KEY (subworkflow_id, semver)
);

CREATE INDEX idx_subworkflows_id
ON subworkflows (subworkflow_id);

CREATE INDEX idx_subworkflows_created_at
ON subworkflows (created_at DESC);

CREATE INDEX idx_subworkflows_updated_at
ON subworkflows (updated_at DESC);

-- ── Workflow execution instances ─────────────────────────────

CREATE TABLE workflow_executions (
    id TEXT PRIMARY KEY NOT NULL CHECK (LENGTH(id) > 0),
    definition_id TEXT NOT NULL CHECK (LENGTH(definition_id) > 0),
    definition_revision BIGINT NOT NULL CHECK (definition_revision >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'completed', 'failed', 'cancelled'
    )),
    node_executions JSONB NOT NULL DEFAULT '[]'::JSONB,
    activated_by TEXT NOT NULL CHECK (LENGTH(activated_by) > 0),
    project TEXT NOT NULL CHECK (LENGTH(project) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error TEXT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    FOREIGN KEY (definition_id) REFERENCES workflow_definitions (id)
);

CREATE INDEX idx_wfe_definition_id
ON workflow_executions (definition_id);

CREATE INDEX idx_wfe_status
ON workflow_executions (status);

CREATE INDEX idx_wfe_updated_at
ON workflow_executions (updated_at DESC);

CREATE INDEX idx_wfe_definition_updated
ON workflow_executions (definition_id, updated_at DESC);

CREATE INDEX idx_wfe_status_updated
ON workflow_executions (status, updated_at DESC);

CREATE INDEX idx_wfe_project
ON workflow_executions (project);

CREATE INDEX idx_wfe_definition_revision
ON workflow_executions (definition_id, definition_revision);

CREATE INDEX idx_we_node_executions
ON workflow_executions USING GIN (node_executions);

-- ── Fine-tuning pipeline runs ───────────────────────────────────
CREATE TABLE fine_tune_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (LENGTH(id) > 0),
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
    stages_completed JSONB NOT NULL DEFAULT '[]'::JSONB
);

CREATE INDEX idx_ftr_stage
ON fine_tune_runs (stage);

CREATE INDEX idx_ftr_started_at
ON fine_tune_runs (started_at DESC);

CREATE INDEX idx_ftr_updated_at
ON fine_tune_runs (updated_at DESC);

-- ── Fine-tuning checkpoints ─────────────────────────────────────
CREATE TABLE fine_tune_checkpoints (
    id TEXT PRIMARY KEY NOT NULL CHECK (LENGTH(id) > 0),
    run_id TEXT NOT NULL REFERENCES fine_tune_runs (id) ON DELETE CASCADE,
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
ON fine_tune_checkpoints (run_id);

CREATE INDEX idx_ftc_active
ON fine_tune_checkpoints (is_active);

CREATE UNIQUE INDEX idx_ftc_single_active
ON fine_tune_checkpoints (is_active)
WHERE is_active = TRUE;

CREATE INDEX idx_ftc_created_at
ON fine_tune_checkpoints (created_at DESC);

-- ── Workflow Definition Versions ─────────────────────────────

CREATE TABLE workflow_definition_versions (
    entity_id TEXT NOT NULL CHECK (LENGTH(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (LENGTH(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (LENGTH(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_wdv_entity_saved
ON workflow_definition_versions (entity_id, saved_at DESC);
CREATE INDEX idx_wdv_content_hash
ON workflow_definition_versions (entity_id, content_hash);

-- ── Decision records (auditable decisions drop-box) ─────────────
CREATE TABLE decision_records (
    id TEXT NOT NULL PRIMARY KEY,
    -- Unpinned like cost_records.task_id. The record of a decision outlives
    -- the task it was about, which is the point of a decisions drop-box;
    -- being unable to delete the task is not.
    task_id TEXT NOT NULL,
    approval_id TEXT,
    executing_agent_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL
    CHECK (reviewer_agent_id != executing_agent_id),
    decision TEXT NOT NULL CHECK (decision IN (
        'approved', 'rejected', 'auto_approved', 'auto_rejected', 'escalated'
    )),
    reason TEXT,
    criteria_snapshot JSONB NOT NULL DEFAULT '[]'::JSONB,
    recorded_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL CHECK (version >= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (task_id, version)
);

CREATE INDEX idx_dr_executing_agent_recorded
ON decision_records (executing_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_reviewer_agent_recorded
ON decision_records (reviewer_agent_id, recorded_at DESC);
CREATE INDEX idx_dr_task_recorded_id
ON decision_records (task_id, recorded_at, id);
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
ON login_attempts (username, attempted_at);
CREATE INDEX idx_la_attempted_at
ON login_attempts (attempted_at);

-- ── Refresh Tokens ───────────────────────────────────────────
CREATE TABLE refresh_tokens (
    token_hash TEXT NOT NULL PRIMARY KEY,
    session_id TEXT NOT NULL
    REFERENCES sessions (session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL
    REFERENCES users (id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_rt_user_id ON refresh_tokens (user_id);
CREATE INDEX idx_rt_session_id ON refresh_tokens (session_id);
CREATE INDEX idx_rt_expires_at ON refresh_tokens (expires_at);
-- Speeds up "revoke every unused refresh token for a session" sweeps
-- (revoke_by_session) which scan once per session_id and filter on
-- used=false; without this composite the planner falls back to the
-- single-column session_id index plus a row-by-row used filter.
CREATE INDEX idx_rt_session_used ON refresh_tokens (session_id, used);

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

CREATE INDEX idx_ro_action_type ON risk_overrides (action_type);
CREATE INDEX idx_ro_active
ON risk_overrides (created_at DESC, expires_at)
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
        (
            status IN ('allowed', 'denied')
            AND resolved_by IS NOT NULL
            AND resolved_at IS NOT NULL
        )
    )
);

CREATE INDEX idx_sv_status_timestamp
ON ssrf_violations (status, timestamp DESC);
CREATE INDEX idx_sv_timestamp ON ssrf_violations (timestamp);
CREATE INDEX idx_sv_hostname ON ssrf_violations (hostname, port);

-- ── Agent identity versions ────────────────────────────────────
CREATE TABLE agent_identity_versions (
    entity_id TEXT NOT NULL CHECK (LENGTH(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (LENGTH(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (LENGTH(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_aiv_entity_saved
ON agent_identity_versions (entity_id, saved_at DESC);
CREATE INDEX idx_aiv_content_hash
ON agent_identity_versions (entity_id, content_hash);

-- ── Budget config versions ───────────────────────────────────────

CREATE TABLE budget_config_versions (
    entity_id TEXT NOT NULL CHECK (LENGTH(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (LENGTH(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (LENGTH(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_bcv_entity_saved
ON budget_config_versions (entity_id, saved_at DESC);
CREATE INDEX idx_bcv_content_hash
ON budget_config_versions (entity_id, content_hash);

-- ── Company versions ─────────────────────────────────────────────

CREATE TABLE company_versions (
    entity_id TEXT NOT NULL CHECK (LENGTH(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (LENGTH(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (LENGTH(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_cv_entity_saved
ON company_versions (entity_id, saved_at DESC);
CREATE INDEX idx_cv_content_hash
ON company_versions (entity_id, content_hash);

-- ── Role versions ────────────────────────────────────────────────

CREATE TABLE role_versions (
    entity_id TEXT NOT NULL CHECK (LENGTH(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (LENGTH(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (LENGTH(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_rv_entity_saved
ON role_versions (entity_id, saved_at DESC);
CREATE INDEX idx_rv_content_hash
ON role_versions (entity_id, content_hash);

-- ── Circuit breaker state ─────────────────────────────────────────

CREATE TABLE circuit_breaker_state (
    pair_key_a TEXT NOT NULL CHECK (LENGTH(pair_key_a) > 0),
    pair_key_b TEXT NOT NULL CHECK (LENGTH(pair_key_b) > 0),
    bounce_count BIGINT NOT NULL DEFAULT 0 CHECK (bounce_count >= 0),
    trip_count BIGINT NOT NULL DEFAULT 0 CHECK (trip_count >= 0),
    opened_at DOUBLE PRECISION,
    PRIMARY KEY (pair_key_a, pair_key_b)
);

-- ── Runtime tool-call failure signals ─────────────────────────
-- Time-decayed per-(provider, model) tool-call failure accumulator for
-- the runtime feedback loop. decayed_at is epoch seconds (the same
-- decay-arithmetic float representation as circuit_breaker_state.opened_at).

CREATE TABLE model_tool_call_signals (
    provider_name TEXT NOT NULL CHECK (LENGTH(provider_name) > 0),
    model_id TEXT NOT NULL CHECK (LENGTH(model_id) > 0),
    failure_score DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (failure_score >= 0),
    decayed_at DOUBLE PRECISION NOT NULL CHECK (decayed_at >= 0),
    PRIMARY KEY (provider_name, model_id)
);

-- ── Externally-sourced model capability evidence ──────────────
-- One row per (source_label, model_identifier, axis): what one published
-- source measured about one model. model_identifier is the source's own
-- string, kept verbatim so an unresolved row stays inspectable rather than
-- vanishing into a failed match. as_of is when the SOURCE measured it and
-- is what staleness is read from; ingested_at is when we read the source.
-- A refresh upserts and never bulk-deletes, so a feed that drops a model
-- or fails outright leaves its last good row ageing visibly rather than
-- silently un-grading the model.

CREATE TABLE model_capability_scores (
    source_label TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(source_label)) > 0),
    model_identifier TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(model_identifier)) > 0),
    axis TEXT NOT NULL CHECK (axis IN ('coding', 'reasoning', 'general')),
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 100),
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_label, model_identifier, axis)
);

-- Resolution reads every source's opinion of one model at once, so the
-- identifier leads; the source-led index serves per-source ingest and the
-- health panel's "how many rows did this source contribute" count.
CREATE INDEX idx_model_capability_scores_model
ON model_capability_scores (model_identifier, axis);
CREATE INDEX idx_model_capability_scores_source
ON model_capability_scores (source_label, as_of DESC);

-- ── Capability sources: per-source ingest status ──────────────
-- The scores say what a source measured; this says whether the source
-- still works. A feed failing for a month still has last month's rows in
-- the table, and without this record the grading built on them looks
-- exactly as healthy as one refreshed an hour ago. last_attempted_at is
-- what the age gate reads, so a broken feed retries on the same cadence
-- as a working one rather than on every request.

CREATE TABLE capability_source_statuses (
    source_label TEXT NOT NULL PRIMARY KEY
    CHECK (CHAR_LENGTH(TRIM(source_label)) > 0),
    last_attempted_at TIMESTAMPTZ,
    last_succeeded_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    rows_read INTEGER NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
    rows_skipped INTEGER NOT NULL DEFAULT 0 CHECK (rows_skipped >= 0),
    scores_written INTEGER NOT NULL DEFAULT 0 CHECK (scores_written >= 0),
    feed_url TEXT NOT NULL DEFAULT ''
);

-- ── Providers: operator-declared failover ─────────────────────
-- Which connection actually served a request, kept past the restart the
-- event log does not survive. Both pairs are recorded in full: "the
-- alternate" stops being an answer the moment the route map is edited.

CREATE TABLE provider_failover_events (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    feature TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(feature)) > 0),
    declared_provider TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(declared_provider)) > 0),
    declared_model TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(declared_model)) > 0),
    served_provider TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(served_provider)) > 0),
    served_model TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(served_model)) > 0),
    trigger_class TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(trigger_class)) > 0),
    trigger_stage TEXT NOT NULL CHECK (trigger_stage IN ('preflight', 'retry')),
    agent_id TEXT,
    task_id TEXT
);

CREATE INDEX idx_provider_failover_events_occurred
ON provider_failover_events (occurred_at DESC);
CREATE INDEX idx_provider_failover_events_feature
ON provider_failover_events (feature, occurred_at DESC);
CREATE INDEX idx_provider_failover_events_declared_provider
ON provider_failover_events (declared_provider, occurred_at DESC);

-- ── Provider latching failures ────────────────────────────────
-- The one call outcome that outlives the window it was measured in, kept
-- past the restart that would otherwise be a second, silent exit from a
-- verdict whose own text says it does not clear without an operator. One
-- row per pair, replaced by each fresh refusal.

CREATE TABLE provider_latched_failures (
    provider_name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(provider_name)) > 0),
    model TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(model)) > 0),
    outcome_class TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(outcome_class)) > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    error_message TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(error_message)) > 0),
    response_time_ms DOUBLE PRECISION NOT NULL CHECK (response_time_ms >= 0),
    agent_id TEXT,
    task_id TEXT,
    PRIMARY KEY (provider_name, model)
);

CREATE INDEX idx_provider_latched_failures_occurred
ON provider_latched_failures (occurred_at DESC);

-- ── Ontology: Entity definitions ──────────────────────────────

CREATE TABLE entity_definitions (
    name TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(name) > 0),
    tier TEXT NOT NULL CHECK (tier IN ('core', 'user')),
    source TEXT NOT NULL CHECK (source IN ('auto', 'config', 'api')),
    definition TEXT NOT NULL DEFAULT '',
    fields JSONB NOT NULL DEFAULT '[]'::JSONB,
    constraints JSONB NOT NULL DEFAULT '[]'::JSONB,
    disambiguation TEXT NOT NULL DEFAULT '',
    relationships JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_by TEXT NOT NULL CHECK (LENGTH(created_by) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_ed_tier
ON entity_definitions (tier);

-- ── Ontology: Entity definition versions ──────────────────────

CREATE TABLE entity_definition_versions (
    entity_id TEXT NOT NULL CHECK (LENGTH(entity_id) > 0),
    version BIGINT NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (LENGTH(content_hash) > 0),
    snapshot JSONB NOT NULL,
    saved_by TEXT NOT NULL CHECK (LENGTH(saved_by) > 0),
    saved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, version)
);

CREATE INDEX idx_edv_entity_saved
ON entity_definition_versions (entity_id, saved_at DESC);
CREATE INDEX idx_edv_content_hash
ON entity_definition_versions (entity_id, content_hash);

-- ── Connection secrets ───────────────────────────────────────
CREATE TABLE connection_secrets (
    secret_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(secret_id) > 0),
    encrypted_value BYTEA NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1 CHECK (key_version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    rotated_at TIMESTAMPTZ
);

-- ── Connections ──────────────────────────────────────────────
CREATE TABLE connections (
    name TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(name) > 0),
    connection_type TEXT NOT NULL CHECK (
        connection_type IN (
            'github', 'gitlab', 'gitea', 'forgejo', 'slack', 'smtp',
            'database', 'generic_http', 'oauth_app', 'a2a_peer', 'llm_provider',
            'tunnel', 'deploy', 'registry'
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
    health_detail TEXT,
    health_latency_ms DOUBLE PRECISION
    CHECK (
        health_latency_ms IS NULL
        OR (
            health_latency_ms >= 0
            AND health_latency_ms < 'Infinity'::DOUBLE PRECISION
        )
    ),
    health_webhook_ingest TEXT NOT NULL DEFAULT 'not_applicable'
    CHECK (
        health_webhook_ingest IN ('not_applicable', 'ready', 'unconfigured')
    ),
    health_retry_after_seconds DOUBLE PRECISION
    CHECK (
        health_retry_after_seconds IS NULL
        OR (
            health_retry_after_seconds > 0
            AND health_retry_after_seconds < 'Infinity'::DOUBLE PRECISION
        )
    ),
    metadata_json JSONB NOT NULL DEFAULT '{}',
    webhook_receipt_retention_days INTEGER
    CHECK (
        webhook_receipt_retention_days IS NULL
        OR webhook_receipt_retention_days >= 0
    ),
    sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    allowed_repos_json JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_connections_type ON connections (connection_type);

-- ── OAuth states ─────────────────────────────────────────────
CREATE TABLE oauth_states (
    state_token TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections (name) ON DELETE CASCADE,
    pkce_verifier TEXT,
    scopes_requested TEXT NOT NULL DEFAULT '',
    redirect_uri TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    connection_name_returned TEXT,
    -- OIDC nonce; nullable: only set for OIDC connections (jwks_uri
    -- configured). Plain-OAuth2 flows leave it NULL.
    nonce TEXT,
    CONSTRAINT oauth_states_consumed_pair CHECK (
        (consumed_at IS NULL AND connection_name_returned IS NULL)
        OR
        (consumed_at IS NOT NULL AND connection_name_returned IS NOT NULL)
    )
);

CREATE INDEX idx_oauth_states_expires ON oauth_states (expires_at);
CREATE INDEX idx_oauth_states_connection ON oauth_states (connection_name);
CREATE INDEX idx_oauth_states_consumed ON oauth_states (consumed_at);

-- ── Webhook receipts ─────────────────────────────────────────
CREATE TABLE webhook_receipts (
    id TEXT NOT NULL PRIMARY KEY,
    connection_name TEXT NOT NULL REFERENCES connections (name) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (LENGTH(TRIM(event_type)) > 0),
    status TEXT NOT NULL DEFAULT 'received',
    received_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    error TEXT
);

CREATE INDEX idx_webhook_receipts_conn_received
ON webhook_receipts (connection_name, received_at DESC);
-- Serves the unfiltered list_items path, which sorts by received_at with no
-- leading connection_name predicate so the composite index above cannot apply.
CREATE INDEX idx_webhook_receipts_received_id
ON webhook_receipts (received_at DESC, id DESC);

-- ── MCP catalog installations ────────────────────────────────
CREATE TABLE mcp_installations (
    catalog_entry_id TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(catalog_entry_id) > 0),
    connection_name TEXT REFERENCES connections (name) ON DELETE SET NULL,
    installed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_mcp_installations_connection
ON mcp_installations (connection_name);


-- ── Custom signal rules ─────────────────────────────────────────
-- Mirror of the SQLite ``custom_rules`` table; existed on SQLite
-- only until the parity sweep.  Boolean ``enabled`` uses native
-- BOOLEAN; JSON target_altitudes uses JSONB.
CREATE TABLE custom_rules (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(id) > 0),
    name TEXT NOT NULL CHECK (LENGTH(TRIM(name)) > 0),
    description TEXT NOT NULL CHECK (LENGTH(TRIM(description)) > 0),
    metric_path TEXT NOT NULL CHECK (LENGTH(TRIM(metric_path)) > 0),
    comparator TEXT NOT NULL CHECK (LENGTH(TRIM(comparator)) > 0),
    threshold DOUBLE PRECISION NOT NULL,
    severity TEXT NOT NULL CHECK (LENGTH(TRIM(severity)) > 0),
    target_altitudes JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX custom_rules_name ON custom_rules (name);
CREATE INDEX idx_custom_rules_enabled ON custom_rules (enabled);

-- ── Approvals ───────────────────────────────────────────────────
-- Mirror of the SQLite ``approvals`` table.  Boolean-ish checks
-- express the same state-machine invariants; JSONB metadata +
-- evidence_package replace TEXT blobs.
CREATE TABLE approvals (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    action_type TEXT NOT NULL CHECK (LENGTH(TRIM(action_type)) > 0),
    title TEXT NOT NULL CHECK (LENGTH(TRIM(title)) > 0),
    description TEXT NOT NULL,
    requested_by TEXT NOT NULL CHECK (LENGTH(TRIM(requested_by)) > 0),
    risk_level TEXT NOT NULL DEFAULT 'medium' CHECK (
        risk_level IN ('low', 'medium', 'high', 'critical')
    ),
    -- Every member of ApprovalSource, and nothing else. A missing member
    -- is not a narrower contract, it is a row the code writes and the
    -- table refuses at insert time, on a path nothing else can recover.
    -- Widening the enum means widening this list in the same change.
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
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    decision_reason TEXT,
    -- Unpinned like cost_records.task_id: a decided approval is a record of
    -- what a person chose, and must not veto removing what they chose about.
    task_id TEXT,
    evidence_package JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    consumed_at TIMESTAMPTZ,
    CHECK (
        (decided_at IS NULL AND decided_by IS NULL)
        OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    CHECK (
        status != 'rejected'
        OR (
            decision_reason IS NOT NULL
            AND LENGTH(TRIM(decision_reason)) > 0
        )
    )
);

CREATE INDEX idx_approvals_status ON approvals (status);
CREATE INDEX idx_approvals_action_type ON approvals (action_type);
CREATE INDEX idx_approvals_risk_level ON approvals (risk_level);
CREATE INDEX idx_approvals_requested_by_status ON approvals (requested_by, status);
CREATE INDEX idx_approvals_status_expires_at ON approvals (status, expires_at);
CREATE INDEX idx_approvals_task_id ON approvals (task_id);
-- Lets "list pending approvals newest-first" (the dashboard inbox)
-- and the operator-driven "show me last N rejected" queries hit one
-- index range scan instead of (idx_approvals_status -> sort by
-- created_at).
CREATE INDEX idx_approvals_status_created_at
ON approvals (status, created_at DESC);
-- Risk / action triage inboxes newest-first: lets the dashboard
-- "high-risk pending, newest first" and "by action type, newest first"
-- views hit one index range scan instead of a single-column index
-- (idx_approvals_risk_level / idx_approvals_action_type) plus a sort.
CREATE INDEX idx_approvals_risk_created_at
ON approvals (risk_level, created_at DESC);
CREATE INDEX idx_approvals_action_created_at
ON approvals (action_type, created_at DESC);

-- ── Conversational clarify-and-propose ──────────────────────────
-- Mirror of the SQLite tables. ``approval_id`` is a plain TEXT
-- reference (NOT a FK): the ApprovalStore is in-memory-first, so the
-- referenced approval may never be persisted -- a FK would spuriously
-- fail. Mirrors the parked_contexts.approval_id precedent.
-- v1 keeps the index footprint minimal (see the SQLite sibling for
-- the same trim).
CREATE TABLE conversations (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    created_by TEXT NOT NULL CHECK (LENGTH(TRIM(created_by)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'proposed', 'closed')
    ),
    kind TEXT NOT NULL DEFAULT 'direct' CHECK (
        kind IN ('direct', 'routed', 'group')
    )
);
-- Serves the unfiltered list_items seek-page (ORDER BY created_at DESC, id DESC).
CREATE INDEX idx_conversations_created_id
ON conversations (created_at DESC, id DESC);
-- Serves the owner-scoped drawer list (WHERE created_by = ? plus the same order).
CREATE INDEX idx_conversations_created_by_created_id
ON conversations (created_by, created_at DESC, id DESC);

CREATE TABLE conversation_turns (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL
    CONSTRAINT fk_ct_conversation REFERENCES conversations (id),
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'agent')),
    content TEXT NOT NULL CHECK (LENGTH(TRIM(content)) > 0),
    author_agent_id TEXT,
    author_name TEXT,
    routed_topic TEXT,
    routing_confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_ct_author_attribution CHECK (
        (author_agent_id IS NULL) = (author_name IS NULL)
        AND (author_agent_id IS NULL OR LENGTH(TRIM(author_agent_id)) > 0)
        AND (author_name IS NULL OR LENGTH(TRIM(author_name)) > 0)
        AND (role != 'agent' OR author_agent_id IS NOT NULL)
        AND (role != 'user' OR author_agent_id IS NULL)
    ),
    CONSTRAINT uq_ct_conversation_sequence UNIQUE (conversation_id, sequence)
);
CREATE INDEX idx_ct_created_at ON conversation_turns (created_at);
CREATE INDEX idx_ct_conversation_sequence
ON conversation_turns (conversation_id, sequence DESC, id DESC);

CREATE TABLE conversation_participants (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL
    CONSTRAINT fk_cpart_conversation REFERENCES conversations (id),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    agent_name TEXT NOT NULL CHECK (LENGTH(TRIM(agent_name)) > 0),
    participant_role TEXT NOT NULL CHECK (LENGTH(TRIM(participant_role)) > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'removed')
    ),
    added_by TEXT NOT NULL CHECK (LENGTH(TRIM(added_by)) > 0),
    added_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_cpart_conversation_agent UNIQUE (conversation_id, agent_id)
);
CREATE INDEX idx_cpart_conversation_status_added
ON conversation_participants (conversation_id, status, added_at ASC, id ASC);

CREATE TABLE conversation_invites (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL
    CONSTRAINT fk_cinv_conversation REFERENCES conversations (id),
    approval_id TEXT NOT NULL CHECK (LENGTH(TRIM(approval_id)) > 0),
    requested_by_agent_id TEXT NOT NULL
    CHECK (LENGTH(TRIM(requested_by_agent_id)) > 0),
    target_agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(target_agent_id)) > 0),
    target_role TEXT,
    reason TEXT NOT NULL CHECK (LENGTH(TRIM(reason)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'accepted', 'declined')
    ),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX idx_cinv_approval_id
ON conversation_invites (approval_id);
CREATE INDEX idx_cinv_conversation_id
ON conversation_invites (conversation_id);
-- At most one PENDING invite per (conversation, target): the app-layer
-- duplicate-pending check (request_invite) has a read-then-insert TOCTOU
-- gap, so two concurrent parks can both pass it; this index makes the DB
-- the final arbiter. It also serves that hot duplicate check, which
-- filters on (conversation_id, target_agent_id, status = 'pending').
CREATE UNIQUE INDEX idx_cinv_one_pending_per_target
ON conversation_invites (conversation_id, target_agent_id)
WHERE status = 'pending';
-- Serves "all invites for agent X across conversations" (filter target_agent_id
-- alone, any status), which the partial pending-only index cannot serve.
CREATE INDEX idx_cinv_target_agent_id
ON conversation_invites (target_agent_id);

-- Pre-flight cost forecasts.
CREATE TABLE cost_forecasts (
    forecast_id TEXT NOT NULL PRIMARY KEY
    CHECK (CHAR_LENGTH(TRIM(forecast_id)) > 0),
    brief_hash TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(brief_hash)) > 0),
    estimated_cost DOUBLE PRECISION NOT NULL CHECK (estimated_cost >= 0),
    lower_bound DOUBLE PRECISION NOT NULL CHECK (lower_bound >= 0),
    upper_bound DOUBLE PRECISION NOT NULL CHECK (upper_bound >= 0),
    currency TEXT NOT NULL CHECK (CHAR_LENGTH(currency) = 3),
    decision TEXT NOT NULL DEFAULT 'pending' CHECK (
        decision IN ('pending', 'approved', 'rejected', 'superseded')
    ),
    decided_at TIMESTAMPTZ,
    decided_by TEXT
    CHECK (decided_by IS NULL OR CHAR_LENGTH(TRIM(decided_by)) > 0),
    ceiling_amount DOUBLE PRECISION
    CHECK (ceiling_amount IS NULL OR ceiling_amount >= 0),
    halt_accumulated_cost DOUBLE PRECISION
    CHECK (halt_accumulated_cost IS NULL OR halt_accumulated_cost >= 0),
    halt_ceiling_amount DOUBLE PRECISION
    CHECK (halt_ceiling_amount IS NULL OR halt_ceiling_amount >= 0),
    halt_currency TEXT
    CHECK (halt_currency IS NULL OR CHAR_LENGTH(halt_currency) = 3),
    halted_at TIMESTAMPTZ,
    -- The work this forecast gated, so approving it means "run this"
    -- rather than "note that it was allowed". NULL for a forecast minted
    -- for a brief that was never gated.
    gated_work_item JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_cf_lower_le_upper CHECK (lower_bound <= upper_bound),
    CONSTRAINT chk_cf_estimate_within_band CHECK (
        estimated_cost >= lower_bound AND estimated_cost <= upper_bound
    ),
    CONSTRAINT chk_cf_halt_all_or_none CHECK (
        (
            halt_accumulated_cost IS NULL AND halt_ceiling_amount IS NULL
            AND halt_currency IS NULL AND halted_at IS NULL
        )
        OR (
            halt_accumulated_cost IS NOT NULL AND halt_ceiling_amount IS NOT NULL
            AND halt_currency IS NOT NULL AND halted_at IS NOT NULL
        )
    ),
    CONSTRAINT chk_cf_decision_timestamp CHECK (
        (decision = 'pending' AND decided_at IS NULL AND decided_by IS NULL)
        OR (decision = 'superseded' AND decided_at IS NOT NULL AND decided_by IS NULL)
        OR (
            decision IN ('approved', 'rejected')
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
        )
    )
);
CREATE UNIQUE INDEX idx_cost_forecasts_unique_pending
ON cost_forecasts (brief_hash)
WHERE decision = 'pending';
CREATE INDEX idx_cost_forecasts_brief_hash
ON cost_forecasts (brief_hash);
CREATE INDEX idx_cost_forecasts_decision
ON cost_forecasts (decision);

-- Org memory: MVCC operation log + materialized snapshot.
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
    author_role TEXT,
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
-- Supports OrgFactRepository.list_by_category and snapshot_at when a
-- category filter is supplied; without this composite the planner
-- chooses the single-column timestamp index then filters category
-- inline (linear in the matching window).
CREATE INDEX idx_oplog_category_ts
ON org_facts_operation_log (category, timestamp DESC);
-- Operation-type audit queries ("all RETRACT ops") scan the whole
-- log without this; the column is low-cardinality but the index lets
-- the planner skip the full table for the (rare) retract sweep.
CREATE INDEX idx_oplog_operation_type
ON org_facts_operation_log (operation_type);

CREATE TABLE org_facts_snapshot (
    fact_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    author_agent_id TEXT,
    author_role TEXT,
    author_is_human BOOLEAN NOT NULL DEFAULT FALSE,
    author_autonomy_level TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    retracted_at TIMESTAMPTZ,
    version INTEGER NOT NULL,
    -- Shared case/Unicode-folded search form of content (Python
    -- normalize_for_search): both backends query this, never SQL LOWER, so
    -- accented term matching stays identical across SQLite and Postgres.
    -- Appended last to match the ALTER TABLE ADD COLUMN in its revision.
    content_normalized TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_snapshot_category ON org_facts_snapshot (category);
CREATE INDEX idx_snapshot_active ON org_facts_snapshot (retracted_at)
WHERE retracted_at IS NULL;
-- "Live facts in category X" is the hot ontology read. The partial
-- index keeps only non-retracted rows so the planner does a single
-- covered range scan instead of (idx_snapshot_category -> filter
-- retracted_at) across the full category.
CREATE INDEX idx_snapshot_category_active
ON org_facts_snapshot (category)
WHERE retracted_at IS NULL;

-- Ontology drift reports.
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

-- Persistent idempotency keys for retry-prone endpoints.
-- A claim row goes through (in_flight) -> (completed | failed); the
-- cached response_body lets a duplicate caller receive the same
-- reply rather than 409. Rows older than expires_at are reaped by
-- the periodic cleanup task.
CREATE TABLE idempotency_keys (
    scope TEXT NOT NULL
    CHECK (LENGTH(TRIM(scope)) > 0 AND LENGTH(scope) <= 64),
    key TEXT NOT NULL
    CHECK (LENGTH(TRIM(key)) > 0 AND LENGTH(key) <= 255),
    -- lint-allow: enum-check-parity -- IdempotencyOutcome.FRESH is the verdict
    -- of a claim attempt ("no record existed"), not a state a row can be in;
    -- the three stored states are the whole vocabulary of this column.
    status TEXT NOT NULL CHECK (status IN ('in_flight', 'completed', 'failed')),
    -- Opaque per-lease ownership token (UUIDv4 hex). Rotated every
    -- time a row is reclaimed (FRESH on an expired/failed prior
    -- entry) so a stale worker that times out and finishes later
    -- cannot CAS-overwrite the new lease's cached response.
    claim_token TEXT NOT NULL CHECK (LENGTH(TRIM(claim_token)) > 0),
    response_hash TEXT,
    -- Cached HTTP response body. Stored as TEXT (not JSONB) so the
    -- bytes round-trip verbatim -- JSONB would canonicalise key
    -- order and whitespace, breaking ``response_hash`` integrity
    -- checks and diverging from the SQLite backend's TEXT
    -- semantics. The service layer enforces JSON-validity on the
    -- way in (via ``json.dumps``); the column is free text on the
    -- DB side because we never query into it via JSONB operators.
    response_body TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > created_at),
    -- Fingerprint (hex SHA-256) of the request that first claimed this key.
    -- Lets the service reject a replay of the same key carrying a different
    -- payload. Nullable: rows written before the column existed, and callers
    -- that do not opt in, leave it NULL.
    request_fingerprint TEXT,
    -- Cached-response invariant: the (response_hash, response_body)
    -- pair is set if and only if status is 'completed'. Catches
    -- buggy writes and corrupt rows loaded from disk before the
    -- service layer sees them.
    CONSTRAINT idempotency_keys_response_cache_check CHECK (
        (
            status = 'completed'
            AND response_hash IS NOT NULL
            AND response_body IS NOT NULL
        )
        OR (
            status IN ('in_flight', 'failed')
            AND response_hash IS NULL
            AND response_body IS NULL
        )
    ),
    PRIMARY KEY (scope, key)
);
CREATE INDEX idx_idempotency_expires ON idempotency_keys (expires_at);

-- ── Provider audit events ─────────────────────────────────────────────
-- Append-only mutation history for provider config writes.  Powers
-- ``GET /api/v1/providers/{name}/audit`` and is written by
-- ``ProviderAuditService.record`` from every mutation entry point on
-- ``ProviderManagementService``.  ``id`` is BIGSERIAL so it doubles
-- as the keyset pagination cursor.  ``payload`` is JSONB; credentials
-- inside payload MUST be masked (``"prefix***last4"``).
CREATE TABLE provider_audit_events (
    id BIGSERIAL PRIMARY KEY,
    provider_name TEXT NOT NULL CHECK (LENGTH(TRIM(provider_name)) > 0),
    event_type TEXT NOT NULL CHECK (LENGTH(TRIM(event_type)) > 0),
    actor_id TEXT NOT NULL CHECK (LENGTH(TRIM(actor_id)) > 0),
    actor_label TEXT NOT NULL CHECK (LENGTH(TRIM(actor_label)) > 0),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_provider_audit_events_provider_id
ON provider_audit_events (provider_name, id DESC);
CREATE INDEX idx_provider_audit_events_occurred
ON provider_audit_events (occurred_at);

-- ── Preset overrides ──────────────────────────────────────────────────
-- Operator overrides on top of in-code provider presets.  Read at
-- preset-resolution time by ``PresetOverrideService.get_effective``.
-- Cross-shape validation (cloud preset rejecting candidate_urls,
-- local preset rejecting base_url) lives in the service layer.
CREATE TABLE preset_overrides (
    preset_name TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(preset_name)) > 0),
    default_models JSONB,
    supported_auth_types JSONB,
    candidate_urls JSONB,
    base_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by TEXT NOT NULL CHECK (LENGTH(TRIM(updated_by)) > 0)
);

-- ── Worker claim dedup ────────────────────────────────────────────────
-- First-write store for ``TaskClaim.idempotency_key``.  Workers consult
-- this table before processing a claim so a JetStream redelivery (ack
-- lost, worker crash) cannot trigger a second execution.  Pruned by
-- ``SeenClaimsRepository.prune_expired`` past the row's ``expires_at``.
CREATE TABLE seen_claims (
    idempotency_key TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(idempotency_key)) > 0),
    claim_id TEXT NOT NULL CHECK (LENGTH(TRIM(claim_id)) > 0),
    seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > seen_at)
);
CREATE INDEX idx_seen_claims_expires_at ON seen_claims (expires_at);

-- Restart-safe project-cost-claim dedup: durable backstop so a
-- JetStream redelivery after a process restart cannot double-bill a project
-- cost aggregate.  CostTracker consults this before a durable increment.
CREATE TABLE project_cost_claim_seen (
    claim_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(claim_id)) > 0),
    project_id TEXT NOT NULL CHECK (LENGTH(TRIM(project_id)) > 0),
    seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > seen_at)
);
CREATE INDEX idx_project_cost_claim_seen_expires_at
ON project_cost_claim_seen (expires_at);

-- Principle-override table for the rollback executor's PromptMutator.
-- Overlays the read-only YAML principle packs loaded by
-- engine/strategy/principles.py so a rollback operation can restore
-- previous principle text at runtime without rewriting the packs.
CREATE TABLE principle_overrides (
    scope TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(scope)) > 0),
    text TEXT NOT NULL CHECK (LENGTH(TRIM(text)) > 0),
    restored_from TEXT NOT NULL CHECK (LENGTH(TRIM(restored_from)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Restart-safety tables: persist sandbox state across process
-- restarts. Backed by single-row-per-key repositories; see the
-- matching ``*_protocol.py`` files for the full semantics. JSON columns
-- are TEXT (not JSONB) so save/get round-trips the serialized strings
-- unchanged across both backends; the tables are tiny (one row per
-- container) so JSONB indexing offers no benefit.

-- Docker sandbox container tracking. The sandbox lifecycle persists
-- one row per managed container (sandbox + optional paired sidecar).
-- Queried via PK lookup (delete) and full-scan load_all() at start;
-- no secondary indexes needed for the expected single-host fleet size.
-- SQLite sibling stores created_at as TEXT; conformance tests pin the
-- round-trip equality across backends.
CREATE TABLE tracked_containers (
    container_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(container_id)) > 0),
    sidecar_id TEXT CHECK (sidecar_id IS NULL OR LENGTH(TRIM(sidecar_id)) > 0),
    created_at TIMESTAMPTZ NOT NULL
);

-- Self-extending toolkit: runtime-authored MCP tool blueprints. A
-- blueprint is a declarative spec (name, capability, JSON Schema) plus a
-- sandbox script body, governed through the TOOL_CREATION proposal
-- altitude. state drives the lifecycle pending -> validated -> active ->
-- retired; the state-correlated timestamps are stamped on transition.
-- SQLite sibling stores JSON columns as TEXT and timestamps as TEXT;
-- the dual-backend conformance suite pins the round-trip equality.
CREATE TABLE dynamic_tools (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    name TEXT NOT NULL UNIQUE CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    description TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(description)) > 0),
    capability TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(capability)) > 0),
    parameters_schema JSONB NOT NULL
    CHECK (JSONB_TYPEOF(parameters_schema) = 'object'),
    script_body TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(script_body)) > 0),
    sandbox_backend TEXT NOT NULL
    CHECK (sandbox_backend IN ('docker', 'subprocess')),
    requires_network BOOLEAN NOT NULL DEFAULT FALSE,
    action_type TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(action_type)) > 0),
    state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending', 'validated', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL,
    validated_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    validation JSONB
    CHECK (validation IS NULL OR JSONB_TYPEOF(validation) = 'object'),
    CHECK (
        (
            state = 'pending'
            AND validated_at IS NULL
            AND activated_at IS NULL
            AND retired_at IS NULL
        )
        OR (
            state = 'validated'
            AND validated_at IS NOT NULL
            AND activated_at IS NULL
            AND retired_at IS NULL
            AND validation IS NOT NULL
        )
        OR (
            state = 'active'
            AND validated_at IS NOT NULL
            AND activated_at IS NOT NULL
            AND retired_at IS NULL
            AND validation IS NOT NULL
        )
        OR (
            state = 'retired'
            AND validated_at IS NOT NULL
            AND activated_at IS NOT NULL
            AND retired_at IS NOT NULL
            AND validation IS NOT NULL
        )
    )
);

CREATE INDEX idx_dynamic_tools_state ON dynamic_tools (state);
CREATE INDEX idx_dynamic_tools_capability ON dynamic_tools (capability);
CREATE INDEX idx_dynamic_tools_sandbox_backend
ON dynamic_tools (sandbox_backend);

CREATE TABLE research_runs (
    run_id TEXT NOT NULL PRIMARY KEY,
    brief_id TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL
    CHECK (status IN (
        'planning', 'retrieving', 'triaging',
        'deduplicating', 'synthesising', 'completed', 'failed'
    )),
    created_at TIMESTAMPTZ NOT NULL,
    run_json TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX idx_research_runs_created_at
ON research_runs (created_at DESC, run_id DESC);

CREATE INDEX idx_research_runs_brief
ON research_runs (brief_id, created_at DESC);

CREATE INDEX idx_research_runs_project
ON research_runs (project_id, created_at DESC);

CREATE TABLE project_charters (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(conversation_id)) > 0),
    created_by TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(created_by)) > 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status TEXT NOT NULL DEFAULT 'drafted' CHECK (
        status IN ('drafted', 'approved', 'cancelled')
    ),
    title TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(title)) > 0),
    brief TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(brief)) > 0),
    goals JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(goals) = 'array'),
    constraints JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(constraints) = 'array'),
    success_criteria JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(success_criteria) = 'array'),
    in_scope JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(in_scope) = 'array'),
    out_of_scope JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(out_of_scope) = 'array'),
    envelope_amount DOUBLE PRECISION NOT NULL CHECK (envelope_amount > 0),
    envelope_currency TEXT NOT NULL CHECK (CHAR_LENGTH(envelope_currency) = 3),
    envelope_deadline TIMESTAMPTZ,
    envelope_time_horizon TEXT
    CHECK (
        envelope_time_horizon IS NULL
        OR CHAR_LENGTH(TRIM(envelope_time_horizon)) > 0
    ),
    assumed_facets JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(assumed_facets) = 'array'),
    project_id TEXT
    CHECK (project_id IS NULL OR CHAR_LENGTH(TRIM(project_id)) > 0),
    proposed_project_name TEXT
    CHECK (
        proposed_project_name IS NULL
        OR CHAR_LENGTH(TRIM(proposed_project_name)) > 0
    ),
    proposed_project_description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    approved_at TIMESTAMPTZ,
    approved_by TEXT
    CHECK (approved_by IS NULL OR CHAR_LENGTH(TRIM(approved_by)) > 0),
    forecast_id TEXT
    CHECK (forecast_id IS NULL OR CHAR_LENGTH(TRIM(forecast_id)) > 0),
    correlation_id TEXT
    CHECK (correlation_id IS NULL OR CHAR_LENGTH(TRIM(correlation_id)) > 0),
    task_id TEXT CHECK (task_id IS NULL OR CHAR_LENGTH(TRIM(task_id)) > 0),
    CONSTRAINT chk_charter_project_binding CHECK (
        (project_id IS NOT NULL AND proposed_project_name IS NULL)
        OR (project_id IS NULL AND proposed_project_name IS NOT NULL)
    ),
    CONSTRAINT chk_charter_approval_coupling CHECK (
        (
            status = 'approved'
            AND approved_at IS NOT NULL AND approved_by IS NOT NULL
            AND forecast_id IS NOT NULL AND correlation_id IS NOT NULL
        )
        OR (
            status != 'approved'
            AND approved_at IS NULL AND approved_by IS NULL
            AND forecast_id IS NULL AND correlation_id IS NULL
            AND task_id IS NULL
        )
    )
);

CREATE INDEX idx_project_charters_status ON project_charters (status);
CREATE INDEX idx_project_charters_project_id ON project_charters (project_id);
CREATE INDEX idx_project_charters_created_by ON project_charters (created_by);
CREATE INDEX idx_project_charters_conversation_id
ON project_charters (conversation_id);
CREATE INDEX idx_project_charters_created_id
ON project_charters (created_at DESC, id DESC);

-- ── Deliverable receipts (provenance bundles) ────────────────
CREATE TABLE deliverable_receipt (
    receipt_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    deliverable_doc_slug TEXT NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    total_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (total_cost >= 0),
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_deliverable_receipt_task
ON deliverable_receipt (task_id);
CREATE INDEX idx_deliverable_receipt_project
ON deliverable_receipt (project_id, issued_at DESC);
CREATE INDEX idx_deliverable_receipt_slug
ON deliverable_receipt (deliverable_doc_slug);

-- ── Knowledge usage records (sources consulted per run) ──────
CREATE TABLE knowledge_usage_record (
    record_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_knowledge_usage_execution
ON knowledge_usage_record (execution_id, recorded_at DESC);
CREATE INDEX idx_knowledge_usage_task
ON knowledge_usage_record (task_id);
CREATE INDEX idx_knowledge_usage_project
ON knowledge_usage_record (project_id, recorded_at DESC);
CREATE INDEX idx_knowledge_usage_source
ON knowledge_usage_record (source_id);

-- ── Code execution records (test runs per run) ───────────────
CREATE TABLE code_execution_record (
    record_id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('general', 'tests', 'lint', 'format', 'dependency')),
    command TEXT NOT NULL,
    returncode INTEGER NOT NULL,
    passed BOOLEAN NOT NULL,
    timed_out BOOLEAN NOT NULL,
    stdout_tail TEXT,
    stderr_tail TEXT,
    executed_at TIMESTAMPTZ NOT NULL,
    -- Parity note: SQLite stores ``timed_out`` as INTEGER 0/1 and writes
    -- this CHECK as ``timed_out = 0``; Postgres stores it as BOOLEAN so
    -- the equivalent predicate is ``NOT timed_out``. The two are
    -- semantically identical -- only the per-backend boolean encoding
    -- differs.
    CHECK (passed = (returncode = 0 AND NOT timed_out))
);

CREATE INDEX idx_code_execution_execution
ON code_execution_record (execution_id, executed_at DESC);
CREATE INDEX idx_code_execution_task
ON code_execution_record (task_id);
CREATE INDEX idx_code_execution_project
ON code_execution_record (project_id, executed_at DESC);

-- Measured per-model benchmark scores.
-- One row per model, keyed by ``model_id``. Each row is a quality
-- score (0..100) with a 95 percent confidence band, measured offline
-- from a recorded eval run and re-recorded by the scoring entry-point.
-- ``source`` provenance (``benchmark:...``) flips the dashboard badge
-- from illustrative to measured; ``suite_version`` / ``cassette_sha256``
-- pin the measurement to a specific brief suite and recorded run.
CREATE TABLE benchmark_scores (
    model_id TEXT NOT NULL PRIMARY KEY
    CHECK (CHAR_LENGTH(TRIM(model_id)) > 0),
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 100),
    confidence_lower DOUBLE PRECISION NOT NULL
    CHECK (confidence_lower >= 0 AND confidence_lower <= 100),
    confidence_upper DOUBLE PRECISION NOT NULL
    CHECK (confidence_upper >= 0 AND confidence_upper <= 100),
    source TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(source)) > 0),
    suite_version TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(suite_version)) > 0),
    cassette_sha256 TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(cassette_sha256)) > 0),
    last_updated TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_bs_score_within_band CHECK (
        confidence_lower <= score AND score <= confidence_upper
    )
);

-- Persisted in-family upgrade recommendations surfaced by the periodic
-- model-refresh service. The recommendation payload + pinned agent ids
-- are JSON; status is a scalar column so the review surface can filter.
CREATE TABLE upgrade_recommendations (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    recommendation_json TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(recommendation_json)) > 0),
    agent_ids_json TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(agent_ids_json)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'auto_applied', 'superseded')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    -- Decision metadata is coupled to status: a pending recommendation
    -- stamps neither column; a decided one (approved / rejected /
    -- auto_applied) or one retired by a reconcile pass (superseded) stamps
    -- both, with a non-blank principal (the system actor for superseded).
    CHECK (
        (
            status = 'pending'
            AND decided_at IS NULL
            AND decided_by IS NULL
        )
        OR (
            status IN ('approved', 'rejected', 'auto_applied', 'superseded')
            AND decided_at IS NOT NULL
            AND decided_by IS NOT NULL
            AND CHAR_LENGTH(TRIM(decided_by)) > 0
        )
    )
);
CREATE INDEX idx_ur_status
ON upgrade_recommendations (status, created_at DESC, id DESC);

CREATE TABLE experiment_variants (
    experiment TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(experiment)) > 0),
    variant TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(variant)) > 0),
    weight INTEGER NOT NULL CHECK (weight >= 1 AND weight <= 1000),
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (experiment, variant)
);
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
CREATE INDEX idx_ab_tests_created_id ON ab_tests (created_at DESC, id ASC);

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
CREATE INDEX idx_pruning_requests_created_agent
ON pruning_requests (created_at ASC, agent_id ASC);

CREATE TABLE active_principles (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    principle_text TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(principle_text)) > 0),
    scope TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(scope)) > 0),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('all', 'role', 'department')),
    evolution_mode TEXT NOT NULL
    CHECK (evolution_mode IN ('org_wide', 'override', 'advisory')),
    severity TEXT NOT NULL
    CHECK (severity IN ('informational', 'warning', 'critical')),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_active_principles_scope ON active_principles (scope_kind, scope);
CREATE INDEX idx_active_principles_created_id
ON active_principles (created_at DESC, id ASC);

CREATE TABLE roles (
    name TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    department TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(department)) > 0),
    required_skills JSONB NOT NULL DEFAULT '[]'::JSONB,
    reports_to TEXT,
    tool_access JSONB NOT NULL DEFAULT '[]'::JSONB,
    system_prompt_template TEXT,
    description TEXT NOT NULL DEFAULT '',
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_roles_department ON roles (department);

CREATE TABLE departments (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    name TEXT NOT NULL UNIQUE CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_departments_created_id
ON departments (created_at DESC, id ASC);
CREATE TABLE evolution_outcomes (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(agent_id)) > 0),
    axis TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(axis)) > 0),
    applied BOOLEAN NOT NULL,
    proposed_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_evolution_outcomes_recorded ON evolution_outcomes (recorded_at DESC);
CREATE INDEX idx_evolution_outcomes_axis ON evolution_outcomes (axis);
CREATE INDEX idx_evolution_outcomes_agent
ON evolution_outcomes (agent_id, recorded_at DESC);
CREATE TABLE org_alerts (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    alert_type TEXT NOT NULL
    CHECK (alert_type IN ('inflection', 'threshold', 'trend')),
    description TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(description)) > 0),
    affected_domains JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(affected_domains) = 'array'),
    signal_context JSONB NOT NULL DEFAULT '{}'::JSONB
    CHECK (JSONB_TYPEOF(signal_context) = 'object'),
    recommended_action TEXT
    CHECK (recommended_action IS NULL OR CHAR_LENGTH(TRIM(recommended_action)) > 0),
    emitted_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_org_alerts_emitted ON org_alerts (emitted_at DESC);
CREATE INDEX idx_org_alerts_severity ON org_alerts (severity, emitted_at DESC);
CREATE INDEX idx_org_alerts_type ON org_alerts (alert_type, emitted_at DESC);
CREATE TABLE audit_chain_entries (
    chain_position BIGINT PRIMARY KEY CHECK (chain_position >= 0),
    event_hash TEXT NOT NULL CHECK (LENGTH(TRIM(event_hash)) > 0),
    previous_hash TEXT NOT NULL CHECK (LENGTH(TRIM(previous_hash)) > 0),
    canonical_payload BYTEA NOT NULL,
    signature BYTEA NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL
);
CREATE TABLE hiring_requests (
    id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    status TEXT NOT NULL CHECK (LENGTH(TRIM(status)) > 0),
    requested_by TEXT NOT NULL CHECK (LENGTH(TRIM(requested_by)) > 0),
    department TEXT NOT NULL CHECK (LENGTH(TRIM(department)) > 0),
    role TEXT NOT NULL CHECK (LENGTH(TRIM(role)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX idx_hiring_requests_status ON hiring_requests (status);
-- One hire under way per role, structurally. The staffing sweep checks first,
-- but it checks an in-memory map, which holds only while one process owns it.
CREATE UNIQUE INDEX idx_hiring_requests_one_open_per_role
ON hiring_requests (role)
WHERE status IN ('pending', 'approved');
CREATE TABLE agent_contributions (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    subtask_id TEXT NOT NULL CHECK (LENGTH(TRIM(subtask_id)) > 0),
    contribution_score DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX idx_agent_contributions_agent
ON agent_contributions (agent_id, id DESC);
CREATE INDEX idx_agent_contributions_subtask
ON agent_contributions (subtask_id, id DESC);

-- Agile sprint records: one row per time-boxed work cycle for an
-- agile_kanban project. Backs the /sprints API and the SprintService that
-- pulls tasks into a sprint and advances its strictly-linear lifecycle
-- (planning -> active -> in_review -> retrospective -> completed).
-- start_date / end_date are the domain model's own ISO-8601 strings, so
-- they are stored verbatim as nullable TEXT on both backends.
CREATE TABLE sprints (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    project TEXT CHECK (project IS NULL OR CHAR_LENGTH(TRIM(project)) > 0),
    name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
    CHECK (status IN ('planning', 'active', 'in_review', 'retrospective', 'completed')),
    sprint_number INTEGER NOT NULL CHECK (sprint_number >= 1),
    duration_days INTEGER NOT NULL CHECK (duration_days >= 1),
    start_date TEXT,
    end_date TEXT,
    task_ids JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(task_ids) = 'array'),
    completed_task_ids JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(completed_task_ids) = 'array'),
    task_points JSONB NOT NULL DEFAULT '{}'::JSONB
    CHECK (JSONB_TYPEOF(task_points) = 'object'),
    story_points_committed DOUBLE PRECISION NOT NULL DEFAULT 0.0
    CHECK (story_points_committed >= 0.0),
    story_points_completed DOUBLE PRECISION NOT NULL DEFAULT 0.0
    CHECK (story_points_completed >= 0.0),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
    CHECK (story_points_completed <= story_points_committed),
    UNIQUE (project, sprint_number)
);
CREATE INDEX idx_sprints_status ON sprints (status);
CREATE INDEX idx_sprints_project_status ON sprints (project, status);
CREATE INDEX idx_sprints_number_id ON sprints (sprint_number DESC, id DESC);
CREATE UNIQUE INDEX idx_sprints_org_wide_number ON sprints (sprint_number)
WHERE project IS NULL;
-- One non-completed sprint per scope. The SprintService asks the same
-- question before auto-creating a sprint ("is anything here not completed"),
-- but a check-then-act guarded only by a per-process lock lets two replicas
-- both pass it, and the answer they act on has to be the database's.
-- COALESCE rather than a bare (project) because both engines treat NULLs as
-- distinct in a unique index, which would leave the org-wide scope
-- unguarded; '' cannot collide with a real project, which the column's own
-- CHECK holds to non-blank.
CREATE UNIQUE INDEX idx_sprints_one_open_per_scope
ON sprints (COALESCE(project, ''))
WHERE status != 'completed';

CREATE TABLE plans (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(project)) > 0),
    -- The project's human name, denormalised for the same reason
    -- objective_title is: an id is a database key, and a surface that has to
    -- resolve one falls back to showing it the moment the resolve fails.
    project_name TEXT NOT NULL
    CONSTRAINT plans_project_name_check CHECK (CHAR_LENGTH(TRIM(project_name)) > 0),
    objective_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(objective_id)) > 0),
    objective_title TEXT NOT NULL
    CONSTRAINT plans_objective_title_check CHECK (CHAR_LENGTH(TRIM(objective_title)) > 0),
    -- RESTRICT, not CASCADE: a plan is a reviewed decision record, and
    -- deleting the objective task should not silently destroy it (nor its
    -- evaluation reports, which cascade off plans). Deleting a task that
    -- still owns a plan is refused so the operator resolves the plan first,
    -- via DELETE /plans/{id}. Without the reference at all, a deleted task
    -- left the plan pointing at nothing, and the orphan still ran to
    -- completion and reached the review queue.
    parent_task_id TEXT NOT NULL
    REFERENCES tasks (id) ON DELETE RESTRICT
    CHECK (CHAR_LENGTH(TRIM(parent_task_id)) > 0),
    items JSONB NOT NULL
    CONSTRAINT plans_items_check CHECK (
        JSONB_TYPEOF(items) = 'array'
        AND (status IN ('planning', 'failed') OR JSONB_ARRAY_LENGTH(items) > 0)
    ),
    task_structure TEXT NOT NULL DEFAULT 'sequential',
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'draft'
    CONSTRAINT plans_status_check CHECK (status IN (
        'planning', 'draft', 'pending_review', 'approved', 'skeleton',
        'executing', 'integrating', 'evaluating', 'completed', 'rejected',
        'superseded', 'failed'
    )),
    forecast_id TEXT,
    review JSONB,
    open_questions JSONB NOT NULL DEFAULT '[]'::JSONB,
    assumptions JSONB NOT NULL DEFAULT '[]'::JSONB,
    objective_criteria JSONB NOT NULL DEFAULT '[]'::JSONB,
    version_history JSONB NOT NULL DEFAULT '[]'::JSONB,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    failure_reason TEXT
    CHECK (failure_reason IS NULL OR CHAR_LENGTH(TRIM(failure_reason)) > 0),
    replan_generation INTEGER NOT NULL DEFAULT 0
    CHECK (replan_generation >= 0),
    -- Which planner produced the items, recorded when a fallback stood in for
    -- the configured strategy so the approval gate shows what is being approved.
    planning_strategy TEXT
    CHECK (planning_strategy IS NULL OR CHAR_LENGTH(TRIM(planning_strategy)) > 0),
    -- Why a seated review panel produced no verdict, so an unreviewed plan is
    -- visibly unreviewed rather than silently blank. Blank is the state both
    -- columns exist to distinguish from absent, so the model types them
    -- ``NotBlankStr | None`` and the row mirrors it.
    review_absent_reason TEXT
    CHECK (
        review_absent_reason IS NULL
        OR CHAR_LENGTH(TRIM(review_absent_reason)) > 0
    ),
    -- Where the decomposition writing this plan has got to. A recursive
    -- decomposition persists its tree once, at the end, so the row reads
    -- PLANNING with zero items for the whole of a run that can last an hour,
    -- and without this the only way to tell a working decomposition from a
    -- hung one was the backend log. NULL is "nothing has reported", which is
    -- a different claim from a zero snapshot, so there is deliberately no
    -- DEFAULT.
    decomposition_progress JSONB
    CONSTRAINT plans_decomposition_progress_check
    CHECK (
        decomposition_progress IS NULL
        OR JSONB_TYPEOF(decomposition_progress) = 'object'
    ),
    -- failure_reason is present iff the plan is FAILED: a FAILED plan must carry
    -- a reason (so Plan Review always shows why), and no other status may carry
    -- one. Mirrors the Plan model validator as the persistence-level backstop.
    CONSTRAINT plans_failure_reason_status_check
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
);
CREATE INDEX idx_plans_status ON plans (status);
CREATE INDEX idx_plans_project ON plans (project);
CREATE INDEX idx_plans_objective ON plans (objective_id);
CREATE INDEX idx_plans_project_status ON plans (project, status, id);
-- The task-delete guard reads `WHERE parent_task_id = ? ORDER BY id LIMIT 1`,
-- so `id` rides the index: equality first, then the ordering, as
-- idx_plans_project_status already does.
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id, id);

-- ── Lifecycle transitions (who moved this initiative, and when) ─
-- Append-only. A plan reaching COMPLETED left no durable actor record, so
-- "only the evaluate stage writes COMPLETED" was provable from a container
-- log and nowhere else. Plans and projects share one ledger because they
-- answer one question.
CREATE TABLE lifecycle_transitions (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('plan', 'project')),
    entity_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(entity_id)) > 0),
    from_status TEXT
    CHECK (from_status IS NULL OR CHAR_LENGTH(TRIM(from_status)) > 0),
    to_status TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(to_status)) > 0),
    requested_by TEXT
    CHECK (requested_by IS NULL OR CHAR_LENGTH(TRIM(requested_by)) > 0),
    reason TEXT CHECK (reason IS NULL OR CHAR_LENGTH(TRIM(reason)) > 0),
    entity_version BIGINT NOT NULL CHECK (entity_version >= 0),
    occurred_at TIMESTAMPTZ NOT NULL
);
-- The read is always "this entity's transitions, newest first", and the
-- tie-break on id is part of that ordering, so both sort keys ride in the
-- index and the query never needs a sort step.
CREATE INDEX idx_lifecycle_transitions_entity
ON lifecycle_transitions (entity_kind, entity_id, occurred_at DESC, id DESC);

-- ── Deleted entities (what the id in a surviving record names) ─
-- Append-only. Spend, metrics, approvals and decision records all name the
-- task they are about; pinning that task with a foreign key made each of
-- them a reason it could never be removed, so a project whose task had once
-- spent money was undeletable and said so with a constraint name. Those
-- pins are gone and the identifier is retained verbatim instead, which
-- leaves exactly one question: what was it. This answers it.
--
-- Written only when a person deletes something. Nothing the system does on
-- its own removes an entity, so nothing the system does on its own writes
-- here, and ``deleted_by`` is NOT NULL to keep it that way.
CREATE TABLE deleted_entities (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    entity_kind TEXT NOT NULL
    CHECK (entity_kind IN ('task', 'plan', 'project')),
    entity_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(entity_id)) > 0),
    display_name TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(display_name)) > 0),
    deleted_by TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(deleted_by)) > 0),
    deleted_at TIMESTAMPTZ NOT NULL,
    -- One tombstone per entity, stated here rather than left to the
    -- writer. The row id is derived from the pair, so the primary key
    -- already enforces it for a writer that derives the same way; this
    -- says it for every writer, so a caller minting its own id cannot add
    -- a second row for the same entity and leave the lookup answering
    -- "what was this" with whichever copy the ordering reached first.
    UNIQUE (entity_kind, entity_id)
);
-- The read is "resolve this identifier", so the lookup key leads. The
-- insert is ON CONFLICT DO NOTHING on the pair above, so a repeated delete
-- of the same entity keeps the first record rather than adding a second.
-- The timestamp rides along to order the unfiltered listing.
CREATE INDEX idx_deleted_entities_lookup
ON deleted_entities (entity_id, entity_kind, deleted_at DESC);

-- ── Initiative evaluation reports (the delivery verdict) ─────
-- The verdict is what decides whether an initiative delivered, so it is
-- a record rather than a log line. Append-only: a re-evaluation is a new
-- attempt with its own row, because overwriting would erase the evidence
-- the replan points at. The unique (plan_id, attempt) key is what makes
-- a lost CAS race cost nothing: the verdict is already on disk.
--
-- Placed after ``plans`` rather than beside its sibling append-only tables
-- because this file is replayed as a migration to check for drift, and
-- Postgres resolves a REFERENCES target at CREATE TABLE time. SQLite
-- resolves it lazily, which is why only the Postgres arm cares.
CREATE TABLE initiative_evaluation_report (
    record_id TEXT NOT NULL PRIMARY KEY,
    -- Cascade rather than an age sweeper: per-plan row count is bounded by
    -- the stage's attempt cap, and purging an attempt of a live plan would
    -- destroy the evidence its replan points at. A deleted plan's verdicts
    -- are unreadable by anything, so those go with it.
    plan_id TEXT NOT NULL
    REFERENCES plans (id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    verdict_summary TEXT NOT NULL
    CHECK (CHAR_LENGTH(TRIM(verdict_summary)) > 0),
    verdicts JSONB NOT NULL,
    -- Parity note: BOOLEAN enforces true/false natively, so no CHECK is
    -- needed here. SQLite has no boolean type, so its sibling column needs
    -- an explicit ``objective_met IN (0, 1)`` to get the same domain.
    objective_met BOOLEAN NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_evaluation_report_attempt UNIQUE (plan_id, attempt)
);

CREATE INDEX idx_evaluation_report_plan
ON initiative_evaluation_report (plan_id, evaluated_at DESC);
CREATE INDEX idx_evaluation_report_project
ON initiative_evaluation_report (project_id, evaluated_at DESC);

CREATE TABLE plan_item_comments (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    -- A comment is a remark ON a plan and has no meaning once the plan is
    -- gone, so it cascades. This is the same orphan class the plans
    -- parent-task reference closes, one table down.
    plan_id TEXT NOT NULL
    REFERENCES plans (id) ON DELETE CASCADE
    CHECK (CHAR_LENGTH(TRIM(plan_id)) > 0),
    item_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(item_id)) > 0),
    author TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(author)) > 0),
    body TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(body)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'human'
    -- lint-allow: enum-check-parity -- typed CommentAuthorKind, not ActorKind;
    -- sharing two spellings with it is coincidence, and nothing writes a
    -- system-authored plan comment.
    CHECK (author_kind IN ('human', 'agent')),
    author_agent_id TEXT
    CHECK (
        (author_agent_id IS NULL OR CHAR_LENGTH(TRIM(author_agent_id)) > 0)
        AND ((author_kind = 'agent') = (author_agent_id IS NOT NULL))
    ),
    reply_to_id TEXT
    REFERENCES plan_item_comments (id) ON DELETE SET NULL
    CHECK (reply_to_id IS NULL OR CHAR_LENGTH(TRIM(reply_to_id)) > 0)
);
CREATE INDEX idx_plan_item_comments_plan_item
ON plan_item_comments (plan_id, item_id, created_at);
CREATE INDEX idx_plan_item_comments_reply
ON plan_item_comments (reply_to_id)
WHERE reply_to_id IS NOT NULL;

CREATE TABLE memory_entries (
    memory_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(memory_id)) > 0),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    namespace TEXT NOT NULL DEFAULT 'default' CHECK (LENGTH(TRIM(namespace)) > 0),
    category TEXT NOT NULL CHECK (LENGTH(TRIM(category)) > 0),
    content TEXT NOT NULL CHECK (LENGTH(TRIM(content)) > 0),
    source TEXT CHECK (source IS NULL OR LENGTH(TRIM(source)) > 0),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    tags JSONB NOT NULL DEFAULT '[]'::JSONB,
    token_count INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);
CREATE INDEX idx_memory_entries_agent ON memory_entries (agent_id, created_at DESC);
CREATE INDEX idx_memory_entries_agent_category ON memory_entries (agent_id, category);
CREATE INDEX idx_memory_entries_namespace ON memory_entries (agent_id, namespace);
CREATE INDEX idx_memory_entries_expires ON memory_entries (expires_at)
WHERE expires_at IS NOT NULL;
CREATE INDEX idx_memory_entries_tags ON memory_entries USING GIN (tags);

CREATE TABLE memory_entry_terms (
    memory_id TEXT NOT NULL
    REFERENCES memory_entries (memory_id) ON DELETE CASCADE,
    term TEXT NOT NULL CHECK (LENGTH(TRIM(term)) > 0),
    term_frequency INTEGER NOT NULL CHECK (term_frequency > 0),
    PRIMARY KEY (memory_id, term)
);
CREATE INDEX idx_memory_entry_terms_term ON memory_entry_terms (term);

-- A backgrounded shell command outlives the tool call that started it, so
-- its record has to outlive the process too: this is what the boot
-- reconciliation sweep reads to tell "still running in a live container"
-- from "orphaned by a hard kill" (see ``reap_orphaned_background_jobs``).
-- ``container_id`` and ``owner_id`` are both indexed: the sandbox layer
-- asks "does this container still have live jobs pinning it open" (keyed
-- on container_id) and "how many live jobs does this owner already have"
-- (the per-owner job cap, keyed on owner_id+status). ``pid`` and
-- ``exit_code`` are nullable: a job starts PENDING with neither, gains a
-- PID once the wrapper confirms the process started, and gains an exit
-- code only once it actually finishes.
CREATE TABLE background_jobs (
    job_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(job_id)) > 0),
    container_id TEXT NOT NULL CHECK (LENGTH(TRIM(container_id)) > 0),
    owner_id TEXT NOT NULL CHECK (LENGTH(TRIM(owner_id)) > 0),
    project_id TEXT CHECK (project_id IS NULL OR LENGTH(TRIM(project_id)) > 0),
    command_repr TEXT NOT NULL CHECK (LENGTH(TRIM(command_repr)) > 0),
    pid INTEGER CHECK (pid IS NULL OR pid > 0),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'completed', 'failed',
        'cancelled', 'timed_out', 'orphaned'
    )),
    exit_code INTEGER,
    output_path TEXT NOT NULL CHECK (LENGTH(TRIM(output_path)) > 0),
    started_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    max_duration_seconds DOUBLE PRECISION NOT NULL CHECK (max_duration_seconds > 0)
);
CREATE INDEX idx_background_jobs_container_status ON background_jobs (container_id, status);
CREATE INDEX idx_background_jobs_owner_status ON background_jobs (owner_id, status);
