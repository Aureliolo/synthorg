-- Promote ISO-text timestamps to TIMESTAMPTZ and JSON-text columns to
-- JSONB so these tables match the Postgres native-type convention used
-- by the rest of the schema, plus composite / coverage indices.
--
-- SQLite keeps TEXT + json.dumps / ISO-8601 (sanctioned native
-- fallback, documented in the schema header inventory). Existing rows
-- hold valid ISO-8601 / JSON text, so every USING cast is total.

-- cost_forecasts: ISO-text timestamps -> TIMESTAMPTZ.
ALTER TABLE cost_forecasts
    DROP CONSTRAINT IF EXISTS cost_forecasts_decided_at_check,
    DROP CONSTRAINT IF EXISTS cost_forecasts_halted_at_check,
    DROP CONSTRAINT IF EXISTS cost_forecasts_created_at_check,
    DROP CONSTRAINT IF EXISTS cost_forecasts_updated_at_check;
ALTER TABLE cost_forecasts
    ALTER COLUMN decided_at TYPE TIMESTAMPTZ USING decided_at::TIMESTAMPTZ,
    ALTER COLUMN halted_at TYPE TIMESTAMPTZ USING halted_at::TIMESTAMPTZ,
    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at::TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at::TIMESTAMPTZ;

-- project_charters: timestamps -> TIMESTAMPTZ; JSON-array columns -> JSONB.
ALTER TABLE project_charters
    DROP CONSTRAINT IF EXISTS project_charters_created_at_check,
    DROP CONSTRAINT IF EXISTS project_charters_updated_at_check,
    DROP CONSTRAINT IF EXISTS project_charters_approved_at_check,
    DROP CONSTRAINT IF EXISTS project_charters_envelope_deadline_check;
ALTER TABLE project_charters
    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at::TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at::TIMESTAMPTZ,
    ALTER COLUMN approved_at TYPE TIMESTAMPTZ USING approved_at::TIMESTAMPTZ,
    ALTER COLUMN envelope_deadline TYPE TIMESTAMPTZ
        USING envelope_deadline::TIMESTAMPTZ;
ALTER TABLE project_charters
    ALTER COLUMN goals DROP DEFAULT,
    ALTER COLUMN goals TYPE JSONB USING goals::JSONB,
    ALTER COLUMN goals SET DEFAULT '[]'::JSONB,
    ALTER COLUMN constraints DROP DEFAULT,
    ALTER COLUMN constraints TYPE JSONB USING constraints::JSONB,
    ALTER COLUMN constraints SET DEFAULT '[]'::JSONB,
    ALTER COLUMN success_criteria DROP DEFAULT,
    ALTER COLUMN success_criteria TYPE JSONB USING success_criteria::JSONB,
    ALTER COLUMN success_criteria SET DEFAULT '[]'::JSONB,
    ALTER COLUMN in_scope DROP DEFAULT,
    ALTER COLUMN in_scope TYPE JSONB USING in_scope::JSONB,
    ALTER COLUMN in_scope SET DEFAULT '[]'::JSONB,
    ALTER COLUMN out_of_scope DROP DEFAULT,
    ALTER COLUMN out_of_scope TYPE JSONB USING out_of_scope::JSONB,
    ALTER COLUMN out_of_scope SET DEFAULT '[]'::JSONB;

-- project_brain_entries: JSON-text columns -> JSONB.
ALTER TABLE project_brain_entries
    ALTER COLUMN related_task_ids DROP DEFAULT,
    ALTER COLUMN related_task_ids TYPE JSONB USING related_task_ids::JSONB,
    ALTER COLUMN related_task_ids SET DEFAULT '[]'::JSONB,
    ALTER COLUMN related_entry_ids DROP DEFAULT,
    ALTER COLUMN related_entry_ids TYPE JSONB USING related_entry_ids::JSONB,
    ALTER COLUMN related_entry_ids SET DEFAULT '[]'::JSONB,
    ALTER COLUMN tags DROP DEFAULT,
    ALTER COLUMN tags TYPE JSONB USING tags::JSONB,
    ALTER COLUMN tags SET DEFAULT '[]'::JSONB,
    ALTER COLUMN citations DROP DEFAULT,
    ALTER COLUMN citations TYPE JSONB USING citations::JSONB,
    ALTER COLUMN citations SET DEFAULT '[]'::JSONB,
    ALTER COLUMN payload TYPE JSONB USING payload::JSONB;

-- Composite / coverage indices for hot list queries.
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
