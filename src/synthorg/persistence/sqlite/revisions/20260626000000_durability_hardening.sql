-- Durability hardening (#2478): six tables that move previously
-- in-memory security/HR/audit runtime state into the durable store so
-- it survives process restarts.
--
-- * trust_states / trust_change_history -- progressive trust state and
--   its immutable audit trail (was TrustService._trust_states /
--   _change_history).
-- * audit_chain_entries -- the tamper-evident hash chain (was
--   HashChain._entries, lost on restart and unverifiable).
-- * promotion_history -- promotion/demotion records used to recompute
--   per-agent cooldown on load (was PromotionService._promotion_history).
-- * hiring_requests -- in-flight hiring requests and their lifecycle
--   status (was HiringService._requests; dangling approvals on restart).
-- * agent_contributions -- coordination contribution accumulator (was
--   PerformanceTracker._contributions, a write-only in-memory list).
--
-- Complex nested models (PromotionRecord, HiringRequest,
-- AgentContribution) round-trip through a JSON `payload` column with a
-- few promoted columns for filtering/ordering; SQLite stores JSON as
-- TEXT validated by JSON_VALID().

CREATE TABLE trust_states (
    agent_id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(agent_id)) > 0),
    global_level TEXT NOT NULL CHECK (LENGTH(TRIM(global_level)) > 0),
    created_at TEXT CHECK (
        created_at IS NULL
        OR created_at LIKE '%+00:00'
        OR created_at LIKE '%Z'
    ),
    category_levels TEXT NOT NULL DEFAULT '{}' CHECK (JSON_VALID(category_levels)),
    trust_score REAL CHECK (trust_score IS NULL OR (trust_score >= 0 AND trust_score <= 1)),
    last_evaluated_at TEXT CHECK (
        last_evaluated_at IS NULL
        OR last_evaluated_at LIKE '%+00:00'
        OR last_evaluated_at LIKE '%Z'
    ),
    last_promoted_at TEXT CHECK (
        last_promoted_at IS NULL
        OR last_promoted_at LIKE '%+00:00'
        OR last_promoted_at LIKE '%Z'
    ),
    last_decay_check_at TEXT CHECK (
        last_decay_check_at IS NULL
        OR last_decay_check_at LIKE '%+00:00'
        OR last_decay_check_at LIKE '%Z'
    ),
    milestone_progress TEXT NOT NULL DEFAULT '{}' CHECK (JSON_VALID(milestone_progress))
);

CREATE TABLE trust_change_history (
    id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    old_level TEXT NOT NULL CHECK (LENGTH(TRIM(old_level)) > 0),
    new_level TEXT NOT NULL CHECK (LENGTH(TRIM(new_level)) > 0),
    category TEXT CHECK (category IS NULL OR LENGTH(TRIM(category)) > 0),
    reason TEXT NOT NULL CHECK (LENGTH(TRIM(reason)) > 0),
    timestamp TEXT NOT NULL CHECK (timestamp LIKE '%+00:00' OR timestamp LIKE '%Z'),
    approval_id TEXT CHECK (approval_id IS NULL OR LENGTH(TRIM(approval_id)) > 0),
    details TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_trust_change_history_agent
ON trust_change_history (agent_id, timestamp DESC);

CREATE TABLE audit_chain_entries (
    chain_position INTEGER PRIMARY KEY CHECK (chain_position >= 0),
    event_hash TEXT NOT NULL CHECK (LENGTH(TRIM(event_hash)) > 0),
    previous_hash TEXT NOT NULL CHECK (LENGTH(TRIM(previous_hash)) > 0),
    canonical_payload BLOB NOT NULL,
    signature BLOB NOT NULL,
    timestamp TEXT NOT NULL CHECK (timestamp LIKE '%+00:00' OR timestamp LIKE '%Z')
);

CREATE TABLE promotion_history (
    id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    direction TEXT NOT NULL CHECK (LENGTH(TRIM(direction)) > 0),
    effective_at TEXT NOT NULL
    CHECK (effective_at LIKE '%+00:00' OR effective_at LIKE '%Z'),
    payload TEXT NOT NULL CHECK (JSON_VALID(payload))
);
CREATE INDEX idx_promotion_history_agent
ON promotion_history (agent_id, effective_at DESC);

CREATE TABLE hiring_requests (
    id TEXT PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    status TEXT NOT NULL CHECK (LENGTH(TRIM(status)) > 0),
    requested_by TEXT NOT NULL CHECK (LENGTH(TRIM(requested_by)) > 0),
    department TEXT NOT NULL CHECK (LENGTH(TRIM(department)) > 0),
    role TEXT NOT NULL CHECK (LENGTH(TRIM(role)) > 0),
    created_at TEXT NOT NULL
    CHECK (created_at LIKE '%+00:00' OR created_at LIKE '%Z'),
    payload TEXT NOT NULL CHECK (JSON_VALID(payload))
);
CREATE INDEX idx_hiring_requests_status ON hiring_requests (status);

CREATE TABLE agent_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    subtask_id TEXT NOT NULL CHECK (LENGTH(TRIM(subtask_id)) > 0),
    contribution_score REAL NOT NULL,
    recorded_at TEXT NOT NULL
    CHECK (recorded_at LIKE '%+00:00' OR recorded_at LIKE '%Z'),
    payload TEXT NOT NULL CHECK (JSON_VALID(payload))
);
CREATE INDEX idx_agent_contributions_agent
ON agent_contributions (agent_id, id DESC);
