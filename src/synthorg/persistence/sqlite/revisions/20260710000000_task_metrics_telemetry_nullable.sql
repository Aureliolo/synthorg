-- Make task_metrics execution telemetry (duration_seconds, cost, turns_used,
-- tokens_used) nullable. A record sourced from a task state transition carries
-- a truthful reliability outcome (is_success) but no measured cost / latency /
-- token telemetry. Storing zero there previously averaged into the efficiency
-- pillar as a perfect score; NULL keeps "not measured" distinct from a genuine
-- zero-cost run so the pillar reports insufficient data instead.
--
-- SQLite cannot drop a column NOT NULL constraint in place, so the table is
-- rebuilt (create-new, copy, drop, rename) and its indexes recreated. task_id
-- keeps its FK to tasks (id); no other table references task_metrics.

CREATE TABLE task_metrics_new (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks (id),
    task_type TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    is_success INTEGER NOT NULL,
    duration_seconds REAL,
    cost REAL,
    currency TEXT NOT NULL DEFAULT 'USD'
    CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
    turns_used INTEGER,
    tokens_used INTEGER,
    quality_score REAL,
    complexity TEXT NOT NULL
);

INSERT INTO task_metrics_new (
    id, agent_id, task_id, task_type, completed_at,
    is_success, duration_seconds, cost, currency, turns_used,
    tokens_used, quality_score, complexity
)
SELECT
    id,
    agent_id,
    task_id,
    task_type,
    completed_at,
    is_success,
    duration_seconds,
    cost,
    currency,
    turns_used,
    tokens_used,
    quality_score,
    complexity
FROM task_metrics;

DROP TABLE task_metrics;

ALTER TABLE task_metrics_new RENAME TO task_metrics;

CREATE INDEX idx_tm_agent_id ON task_metrics (agent_id);
CREATE INDEX idx_tm_completed_at ON task_metrics (completed_at);
CREATE INDEX idx_tm_agent_completed
ON task_metrics (agent_id, completed_at);
