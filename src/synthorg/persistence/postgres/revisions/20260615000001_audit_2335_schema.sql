-- depends: 20260614000002_task_requested_by_user_id

-- Restart-safe project-cost-claim dedup (audit 133).
--
-- Durable backstop against double-billing: CostTracker dedups accepted
-- CostRecord.claim_id values in an in-memory LRU, but that LRU is empty
-- after a crash/OOM/container restart, so a JetStream redelivery of an
-- already-billed cost event would otherwise re-run the durable project
-- cost increment. CostTracker consults this table before incrementing so
-- the guard survives a restart. See ../schema.sql and
-- project_cost_claim_seen_protocol.py for the full rationale.
CREATE TABLE project_cost_claim_seen (
    claim_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(claim_id)) > 0),
    project_id TEXT NOT NULL CHECK (length(trim(project_id)) > 0),
    seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > seen_at)
);
CREATE INDEX idx_project_cost_claim_seen_expires_at
ON project_cost_claim_seen (expires_at);

-- Backend parity (audit 61): SQLite's subworkflows.updated_at carries an
-- epoch sentinel DEFAULT; mirror it on Postgres so an INSERT that omits
-- updated_at behaves identically across backends instead of failing the
-- NOT NULL constraint.
ALTER TABLE subworkflows
ALTER COLUMN updated_at SET DEFAULT '1970-01-01T00:00:00+00:00'::TIMESTAMPTZ;
