-- depends: 20260617000001_upgrade_recommendations

-- Persist the budget-resume + provenance fields that the Task model
-- already carries but both task repos silently dropped: hard_ceiling and
-- forecast_id drive in-loop budget enforcement on resume, source and
-- middleware_override carry pipeline provenance, and metadata holds
-- pipeline labels.
ALTER TABLE tasks ADD COLUMN hard_ceiling DOUBLE PRECISION;
ALTER TABLE tasks ADD COLUMN forecast_id TEXT;
ALTER TABLE tasks ADD COLUMN source TEXT;
ALTER TABLE tasks ADD COLUMN middleware_override JSONB;
ALTER TABLE tasks ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::JSONB;

-- flight_recorder_frames.cost was NUMERIC(12,6) while every other money
-- column maps SQLite REAL -> Postgres DOUBLE PRECISION. Align it so the
-- two backends round-trip identically.
-- (approvals.source widening is a no-op here: Postgres already widened it
--  in 20260531; the SQLite sibling carries the matching widening.)
ALTER TABLE flight_recorder_frames
ALTER COLUMN cost TYPE DOUBLE PRECISION USING cost::DOUBLE PRECISION;

-- Time-window scans over conversation turns had no created_at index.
CREATE INDEX idx_ct_created_at ON conversation_turns (created_at);

-- The participant list query filters (conversation_id, status) and orders
-- by (added_at ASC, id ASC); the old conversation_id-only index forced a
-- sort. The composite fully covers the filter + sort, so it supersedes the
-- narrower single-column index.
DROP INDEX idx_cpart_conversation_id;
CREATE INDEX idx_cpart_conversation_status_added
ON conversation_participants (conversation_id, status, added_at ASC, id ASC);

-- node_executions is queried with JSONB containment; a GIN index turns those
-- scans into index lookups.
CREATE INDEX idx_we_node_executions
ON workflow_executions USING GIN (node_executions);

-- list_by_user filters (user_id, revoked) and orders by
-- (created_at DESC, session_id ASC) but no index covered the sort, forcing a
-- per-query sort. This covering index serves the filter + sort directly;
-- the existing (user_id, revoked, expires_at) index is retained because the
-- active-session count + cleanup paths still rely on the expires_at suffix.
CREATE INDEX idx_sessions_user_created
ON sessions (user_id, revoked, created_at DESC, session_id ASC);
