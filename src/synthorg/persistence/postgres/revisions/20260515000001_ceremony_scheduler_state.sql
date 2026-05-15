-- depends: 20260513000002_principle_overrides

-- JSON blob columns kept as TEXT (not JSONB) so save/get round-trip
-- the serialized strings unchanged across both backends; the table is
-- one row per sprint so JSONB indexing offers no benefit here.
CREATE TABLE ceremony_scheduler_state (
    sprint_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(sprint_id)) > 0),
    completion_counters_json TEXT NOT NULL,
    fired_once_triggers_json TEXT NOT NULL,
    total_completions INTEGER NOT NULL CHECK(total_completions >= 0),
    velocity_history_json TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
