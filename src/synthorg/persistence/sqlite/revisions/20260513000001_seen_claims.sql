-- Worker claim dedup table for TaskClaim.idempotency_key.
--
-- See docs/reference/persistence-boundary.md and the canonical schema
-- at ../schema.sql for the full per-backend rationale.  Workers
-- consult this table before processing a JetStream-delivered claim so
-- a redelivery cannot trigger a second execution.
CREATE TABLE seen_claims (
    idempotency_key TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(idempotency_key)) > 0),
    claim_id TEXT NOT NULL CHECK(length(trim(claim_id)) > 0),
    seen_at TEXT NOT NULL CHECK(length(trim(seen_at)) > 0),
    expires_at TEXT NOT NULL CHECK(length(trim(expires_at)) > 0)
);
CREATE INDEX idx_seen_claims_expires_at ON seen_claims(expires_at);
