-- depends: 20260614000002_task_requested_by_user_id

-- Restart-safe project-cost-claim dedup.
--
-- Durable backstop against double-billing: CostTracker dedups accepted
-- CostRecord.claim_id values in an in-memory LRU, but that LRU is empty
-- after a crash/OOM/container restart, so a JetStream redelivery of an
-- already-billed cost event would otherwise re-run the durable project
-- cost increment. CostTracker consults this table before incrementing so
-- the guard survives a restart. See ../schema.sql and
-- project_cost_claim_seen_protocol.py for the full rationale.
CREATE TABLE project_cost_claim_seen (
    claim_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(claim_id)) > 0),
    project_id TEXT NOT NULL CHECK (LENGTH(TRIM(project_id)) > 0),
    seen_at TEXT NOT NULL CHECK (LENGTH(TRIM(seen_at)) > 0),
    expires_at TEXT NOT NULL CHECK (LENGTH(TRIM(expires_at)) > 0),
    CHECK (expires_at > seen_at)
);
CREATE INDEX idx_project_cost_claim_seen_expires_at
ON project_cost_claim_seen (expires_at);

-- Backend parity: widen approvals.source to match the Postgres
-- domain so a persistent-SQLite ApprovalStore can hold conversational-
-- interface rows ('conversational_intake' / 'conversational_invite').
-- SQLite cannot ALTER a column CHECK in place, so rebuild the table.
-- No table references approvals via foreign key, so the drop-and-rename
-- is safe; the outbound task_id FK is re-declared on the new table.
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
            'conversational_intake', 'conversational_invite'
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
    task_id TEXT CONSTRAINT fk_approvals_task_id REFERENCES tasks (id),
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
