-- depends: 20260513000002_principle_overrides

CREATE TABLE ceremony_scheduler_state (
    sprint_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(sprint_id)) > 0),
    completion_counters_json TEXT NOT NULL,
    fired_once_triggers_json TEXT NOT NULL,
    total_completions INTEGER NOT NULL CHECK(total_completions >= 0),
    velocity_history_json TEXT NOT NULL,
    updated_at TEXT NOT NULL CHECK(length(trim(updated_at)) > 0)
);
