-- Durability hardening (#2478): six tables that move previously
-- in-memory security/HR/audit runtime state into the durable store so
-- it survives process restarts. Postgres twin of the SQLite revision:
-- JSONB for JSON columns, TIMESTAMPTZ for timestamps, BYTEA for the
-- audit-chain binary payload/signature, BIGSERIAL for the contributions
-- surrogate key.

CREATE TABLE trust_states (
    agent_id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(agent_id)) > 0),
    global_level TEXT NOT NULL CHECK (LENGTH(TRIM(global_level)) > 0),
    created_at TIMESTAMPTZ,
    category_levels JSONB NOT NULL DEFAULT '{}'::JSONB,
    trust_score DOUBLE PRECISION CHECK (
        trust_score IS NULL OR (trust_score >= 0 AND trust_score <= 1)
    ),
    last_evaluated_at TIMESTAMPTZ,
    last_promoted_at TIMESTAMPTZ,
    last_decay_check_at TIMESTAMPTZ,
    milestone_progress JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE trust_change_history (
    id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    old_level TEXT NOT NULL CHECK (LENGTH(TRIM(old_level)) > 0),
    new_level TEXT NOT NULL CHECK (LENGTH(TRIM(new_level)) > 0),
    category TEXT CHECK (category IS NULL OR LENGTH(TRIM(category)) > 0),
    reason TEXT NOT NULL CHECK (LENGTH(TRIM(reason)) > 0),
    timestamp TIMESTAMPTZ NOT NULL,
    approval_id TEXT CHECK (approval_id IS NULL OR LENGTH(TRIM(approval_id)) > 0),
    details TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_trust_change_history_agent
ON trust_change_history (agent_id, timestamp DESC);

CREATE TABLE audit_chain_entries (
    position BIGINT PRIMARY KEY CHECK (position >= 0),
    event_hash TEXT NOT NULL CHECK (LENGTH(TRIM(event_hash)) > 0),
    previous_hash TEXT NOT NULL CHECK (LENGTH(TRIM(previous_hash)) > 0),
    canonical_payload BYTEA NOT NULL,
    signature BYTEA NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL
);

CREATE TABLE promotion_history (
    id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    direction TEXT NOT NULL CHECK (LENGTH(TRIM(direction)) > 0),
    effective_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX idx_promotion_history_agent
ON promotion_history (agent_id, effective_at DESC);

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
