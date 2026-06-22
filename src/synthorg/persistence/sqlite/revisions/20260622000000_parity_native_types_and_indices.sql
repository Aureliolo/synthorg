-- Composite / coverage indices for hot list queries.
--
-- SQLite keeps its TEXT + json.dumps / ISO-8601 encoding, so no column
-- types change on this backend; only indices do. The Postgres sibling
-- of this revision additionally promotes ISO-text timestamps to
-- TIMESTAMPTZ and JSON-text columns to JSONB to match the Postgres
-- native-type convention. The cross-backend TEXT-vs-native divergence
-- is the sanctioned fallback documented in the schema header inventory.

CREATE INDEX idx_ct_conversation_sequence
ON conversation_turns (conversation_id, sequence DESC, id DESC);

CREATE INDEX idx_dynamic_tools_sandbox_backend
ON dynamic_tools (sandbox_backend);

DROP INDEX IF EXISTS idx_active_principles_created;
CREATE INDEX idx_active_principles_created_id
ON active_principles (created_at DESC, id ASC);

DROP INDEX IF EXISTS idx_departments_created;
CREATE INDEX idx_departments_created_id
ON departments (created_at DESC, id ASC);

DROP INDEX IF EXISTS idx_ab_tests_created;
CREATE INDEX idx_ab_tests_created_id
ON ab_tests (created_at DESC, id ASC);

DROP INDEX IF EXISTS idx_pruning_requests_created;
CREATE INDEX idx_pruning_requests_created_agent
ON pruning_requests (created_at ASC, agent_id ASC);
