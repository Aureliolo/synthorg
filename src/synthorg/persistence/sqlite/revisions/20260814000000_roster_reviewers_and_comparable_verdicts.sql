-- The reviewer became a roster agent, and these facts followed it.
--
-- 1. A task can now be parked because nobody holds a gate's role.
--
-- ``reviewer_unstaffed`` is a different park from ``oracle_escalated``, and
-- the difference is load-bearing: an escalation is answered by a human and
-- must not be re-judged, while an unstaffed park is answered by staffing the
-- role and MUST be re-judged once somebody holds it. Conflating them would
-- let a task that never had a review reach COMPLETED on a human decision
-- nobody was ever asked for. ``red_team_unstaffed`` is the same condition on
-- the adversarial gate, kept separate because the two name different roles
-- and the staffing sweep needs to know which one a park waits on. SQLite
-- cannot alter a CHECK, so ``tasks`` is rebuilt (create-new, copy, drop,
-- rename) and its indexes recreated.
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
--
-- 4. Nobody is named when nobody reviewed.
--
-- ``completion_oracle_reports.reviewer_agent_id`` was NOT NULL, which left
-- the gate no way to record an escalation that happened BECAUSE no reviewer
-- ran: it had to invent an id, and that id then answered a per-reviewer
-- query as though it were an agent. Both party columns become nullable and
-- the distinctness CHECK guards the both-present case, matching the twin
-- this revision adds to ``red_team_reports``. The capability columns gain
-- the ladder CHECK the schema already applies to that vocabulary elsewhere,
-- so a tier the reader would silently drop cannot be written.
--
-- 5. The pair an operator bound for grounding follows the key that reads it.
--
-- One setting bound the adversary AND its grounding checker. The adversary
-- is a roster agent running on its own pair, so only the grounding checker
-- still needs one, under a key that says so. The stored value is unchanged
-- in meaning, and dropping it would silently degrade grounding to the
-- heuristic on the first boot after upgrade.
--
-- OPERATIONAL NOTES
--
-- This revision runs with foreign-key enforcement OFF, which is yoyo's
-- default and is load-bearing here: ``DROP TABLE tasks`` performs an
-- implicit delete, and ``plans.parent_task_id`` references it ON DELETE
-- RESTRICT, so the drop fails outright with enforcement on. The pragma is a
-- no-op inside a transaction and ``defer_foreign_keys`` does not rescue it,
-- because RESTRICT is immediate. The application's own connection does set
-- ``PRAGMA foreign_keys = ON``; the migration runner deliberately does not.
--
-- Both rebuilds copy the whole table under the write lock, so an upgrade on
-- an instance with a large ``tasks`` table pays a one-off slow boot.
--
-- Rebuilt tables end up with columns in declaration order, which for a
-- migrated database differs from a freshly installed one. Every repository
-- names its columns explicitly, so this is invisible to the application and
-- to the drift gate, which compares by name.

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
            'oracle_escalated',
            'wave_released',
            'reviewer_unstaffed',
            'red_team_unstaffed'
        )
    )
);

INSERT INTO tasks_new (
    id, title, description, type, priority, project, plan_id, plan_item_id,
    created_by, requested_by_user_id, assigned_to, status,
    estimated_complexity, budget_limit, deadline, max_retries, parent_task_id,
    task_structure, coordination_topology, reviewers, dependencies,
    artifacts_expected, acceptance_criteria, delegation_chain, hard_ceiling,
    forecast_id, source, middleware_override, metadata, hard_token_ceiling,
    blocked_reason
)
SELECT
    id,
    title,
    description,
    type,
    priority,
    project,
    plan_id,
    plan_item_id,
    created_by,
    requested_by_user_id,
    assigned_to,
    status,
    estimated_complexity,
    budget_limit,
    deadline,
    max_retries,
    parent_task_id,
    task_structure,
    coordination_topology,
    reviewers,
    dependencies,
    artifacts_expected,
    acceptance_criteria,
    delegation_chain,
    hard_ceiling,
    forecast_id,
    source,
    middleware_override,
    metadata,
    hard_token_ceiling,
    blocked_reason
FROM tasks;

DROP TABLE tasks;

ALTER TABLE tasks_new RENAME TO tasks;

CREATE INDEX idx_tasks_status ON tasks (status);
CREATE INDEX idx_tasks_assigned_to ON tasks (assigned_to);
CREATE INDEX idx_tasks_project ON tasks (project);
CREATE INDEX idx_tasks_plan_id ON tasks (plan_id);

CREATE TABLE completion_oracle_reports_new (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    reviewer_agent_id TEXT,
    executor_agent_id TEXT CHECK (
        reviewer_agent_id IS NULL
        OR executor_agent_id IS NULL
        OR executor_agent_id != reviewer_agent_id
    ),
    reviewer_provider TEXT,
    reviewer_model_id TEXT,
    reviewer_capability TEXT CHECK (
        reviewer_capability IS NULL
        OR reviewer_capability IN ('basic', 'capable', 'expert')
    ),
    verdict TEXT NOT NULL CHECK (
        verdict IN ('approve', 'approve_with_notes', 'reject', 'escalate')
    ),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    report_summary TEXT NOT NULL,
    report_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

INSERT INTO completion_oracle_reports_new (
    report_id, execution_id, task_id, reviewer_agent_id, executor_agent_id,
    verdict, finding_count, report_summary, report_json, recorded_at
)
SELECT
    report_id,
    execution_id,
    task_id,
    reviewer_agent_id,
    executor_agent_id,
    verdict,
    finding_count,
    report_summary,
    report_json,
    recorded_at
FROM completion_oracle_reports;

DROP TABLE completion_oracle_reports;

ALTER TABLE completion_oracle_reports_new RENAME TO completion_oracle_reports;

CREATE INDEX idx_cor_task_id ON completion_oracle_reports (task_id, recorded_at DESC);
CREATE INDEX idx_cor_verdict ON completion_oracle_reports (verdict, recorded_at DESC);
CREATE INDEX idx_cor_recorded_at ON completion_oracle_reports (recorded_at DESC);
CREATE INDEX idx_cor_execution_id
ON completion_oracle_reports (execution_id, recorded_at DESC);
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
    red_team_capability TEXT CHECK (
        red_team_capability IS NULL
        OR red_team_capability IN ('basic', 'capable', 'expert')
    ),
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

-- Guarded so an install that already holds the new key keeps its own value.
INSERT INTO settings (namespace, key, value, updated_at)
SELECT
    'security' AS target_namespace,
    'grounding_model' AS target_key,
    value,
    updated_at
FROM settings
WHERE
    namespace = 'security'
    AND key = 'red_team_model'
    AND NOT EXISTS (
        SELECT 1 FROM settings AS held
        WHERE held.namespace = 'security' AND held.key = 'grounding_model'
    );

DELETE FROM settings
WHERE namespace = 'security' AND key = 'red_team_model';
