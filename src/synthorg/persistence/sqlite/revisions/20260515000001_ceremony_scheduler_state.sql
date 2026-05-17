-- depends: 20260513000002_principle_overrides

-- WP-1 restart-safety tables: persist scheduler / cooldown / sandbox state
-- across process restarts. See docs/decisions/0001-repository-protocol-
-- consolidation.md and the per-table protocol files for the design.

CREATE TABLE ceremony_scheduler_state (
    sprint_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(sprint_id)) > 0),
    completion_counters_json TEXT NOT NULL,
    fired_once_triggers_json TEXT NOT NULL,
    total_completions INTEGER NOT NULL CHECK(total_completions >= 0),
    velocity_history_json TEXT NOT NULL,
    updated_at TEXT NOT NULL CHECK(length(trim(updated_at)) > 0)
);

CREATE TABLE meeting_cooldown (
    meeting_type_name TEXT NOT NULL PRIMARY KEY CHECK(length(trim(meeting_type_name)) > 0),
    last_triggered_at TEXT NOT NULL CHECK(length(trim(last_triggered_at)) > 0)
);

CREATE TABLE tracked_containers (
    container_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(container_id)) > 0),
    sidecar_id TEXT CHECK(sidecar_id IS NULL OR length(trim(sidecar_id)) > 0),
    created_at TEXT NOT NULL CHECK(length(trim(created_at)) > 0)
);
