-- The reviewer became a roster agent, and three facts followed it.
--
-- 1. A task can now be parked because nobody holds the reviewer role.
--
-- ``reviewer_unstaffed`` is a different park from ``oracle_escalated``, and
-- the difference is load-bearing: an escalation is answered by a human and
-- must not be re-judged, while an unstaffed park is answered by staffing the
-- role and MUST be re-judged once somebody holds it. Conflating them would
-- let a task that never had a review reach COMPLETED on a human decision
-- nobody was ever asked for. SQLite cannot alter a CHECK, so ``tasks`` is
-- rebuilt (create-new, copy, drop, rename) and its indexes recreated.
--
-- 2. Which model produced a verdict.
--
-- Verdict quality is compared per agent AND per model, and the agent's
-- current roster binding is not evidence of what ran months ago. The three
-- reviewer columns are nullable because rows written before this change
-- genuinely do not know; NULL is the honest value, never a stand-in.
--
-- 3. Who attacked a deliverable, and who wrote it.
--
-- ``red_team_reports`` recorded neither, so the red-team gate had no
-- structural no-self-attack guard at all: its twin
-- ``completion_oracle_reports`` has carried ``CHECK (executor <> reviewer)``
-- since it was created. Both id columns are nullable for the same reason as
-- above (historical rows cannot name either party), and the CHECK therefore
-- guards the both-present case, which is every row written from now on.
--
-- The table also moves to a surrogate ``report_id``, exactly as
-- ``completion_oracle_reports`` did in 20260813000000 and for the same
-- reason: the gate runs again whenever a task is decided, re-opened and
-- decided again, and an ``execution_id`` primary key made the second
-- (superseding) verdict collide and be swallowed while the stale row stayed
-- the only durable record.

CREATE TABLE tasks_new (
    id TEXT NOT NULL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    project TEXT NOT NULL,
    plan_id TEXT,
    plan_item_id TEXT,
    created_by TEXT NOT NULL,
    requested_by_user_id TEXT,
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    estimated_complexity TEXT NOT NULL DEFAULT 'medium',
    budget_limit REAL NOT NULL DEFAULT 0.0,
    deadline TEXT,
    max_retries INTEGER NOT NULL DEFAULT 1,
    parent_task_id TEXT,
    task_structure TEXT,
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    reviewers TEXT NOT NULL DEFAULT '[]',
    dependencies TEXT NOT NULL DEFAULT '[]',
    artifacts_expected TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    delegation_chain TEXT NOT NULL DEFAULT '[]',
    hard_ceiling REAL,
    forecast_id TEXT,
    source TEXT,
    middleware_override TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    hard_token_ceiling INTEGER CHECK (hard_token_ceiling >= 0),
    blocked_reason TEXT CHECK (
        blocked_reason IN (
            'oracle_escalated', 'wave_released', 'reviewer_unstaffed'
        )
    )
);

INSERT INTO tasks_new SELECT * FROM tasks;

DROP TABLE tasks;

ALTER TABLE tasks_new RENAME TO tasks;

CREATE INDEX idx_tasks_status ON tasks (status);
CREATE INDEX idx_tasks_assigned_to ON tasks (assigned_to);
CREATE INDEX idx_tasks_project ON tasks (project);
CREATE INDEX idx_tasks_plan_id ON tasks (plan_id);

ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_provider TEXT;
ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_model_id TEXT;
ALTER TABLE completion_oracle_reports ADD COLUMN reviewer_capability TEXT;

CREATE INDEX idx_cor_reviewer_agent_id
ON completion_oracle_reports (reviewer_agent_id, recorded_at DESC);

CREATE TABLE red_team_reports_new (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    red_team_agent_id TEXT,
    executor_agent_id TEXT,
    red_team_provider TEXT,
    red_team_model_id TEXT,
    red_team_capability TEXT,
    verdict TEXT NOT NULL CHECK (
        verdict IN ('pass', 'pass_with_findings', 'block')
    ),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    report_summary TEXT NOT NULL,
    report_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        red_team_agent_id IS NULL
        OR executor_agent_id IS NULL
        OR executor_agent_id != red_team_agent_id
    )
);

INSERT INTO red_team_reports_new (
    execution_id, task_id, verdict, finding_count, report_summary,
    report_json, recorded_at
)
SELECT
    execution_id,
    task_id,
    verdict,
    finding_count,
    report_summary,
    report_json,
    recorded_at
FROM red_team_reports;

DROP TABLE red_team_reports;

ALTER TABLE red_team_reports_new RENAME TO red_team_reports;

CREATE INDEX idx_rtr_task_id ON red_team_reports (task_id, recorded_at DESC);
CREATE INDEX idx_rtr_verdict ON red_team_reports (verdict, recorded_at DESC);
CREATE INDEX idx_rtr_recorded_at ON red_team_reports (recorded_at DESC);
CREATE INDEX idx_rtr_execution_id
ON red_team_reports (execution_id, recorded_at DESC);
CREATE INDEX idx_rtr_red_team_agent_id
ON red_team_reports (red_team_agent_id, recorded_at DESC);
