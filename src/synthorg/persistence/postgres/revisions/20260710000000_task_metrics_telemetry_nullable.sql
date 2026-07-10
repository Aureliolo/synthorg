-- Make task_metrics execution telemetry (duration_seconds, cost, turns_used,
-- tokens_used) nullable. A record sourced from a task state transition carries
-- a truthful reliability outcome (is_success) but no measured cost / latency /
-- token telemetry. Storing zero there previously averaged into the efficiency
-- pillar as a perfect score; NULL keeps "not measured" distinct from a genuine
-- zero-cost run so the pillar reports insufficient data instead.

ALTER TABLE task_metrics ALTER COLUMN duration_seconds DROP NOT NULL;
ALTER TABLE task_metrics ALTER COLUMN cost DROP NOT NULL;
ALTER TABLE task_metrics ALTER COLUMN turns_used DROP NOT NULL;
ALTER TABLE task_metrics ALTER COLUMN tokens_used DROP NOT NULL;
