-- A park the loop performs and the database refuses.
--
-- ``BlockedReason.NO_CAPABLE_AGENT`` names the outcome of routing finding
-- nobody the work could go to: no agent the stakes admit, at any rung, scored
-- above the floor. The work is still wanted and the row is still good, so the
-- task parks and waits on an operator rather than failing. One writer persists
-- it (``engine/coordination/service.py``); ``engine/review_staffing/
-- reconciler.py`` logs it and ``engine/review_staffing/unroutable.py`` selects
-- on it, so both depend on the write landing without performing it. The public
-- ``Task`` DTO has carried the member in its enum since it was added.
--
-- The CHECK did not list it, so every one of those parks violated the
-- constraint on write: the insert raised, the task never reached BLOCKED, and
-- the reason an operator needed to see (hire, re-bind a model, or revise the
-- plan item) was the one reason the archive could not hold.
--
-- Widening a CHECK admits values it previously refused, so no existing row can
-- fail it and nothing is rewritten. SQLite cannot alter a CHECK, so ``tasks``
-- is rebuilt (create-new, copy, drop, rename) and its indexes recreated, the
-- same shape 20260813000000 and 20260814000000 used on this table.
--
-- This revision runs with foreign-key enforcement OFF, which is yoyo's default
-- and is load-bearing here: ``DROP TABLE tasks`` performs an implicit delete
-- and ``plans.parent_task_id`` references it ON DELETE RESTRICT, so the drop
-- fails outright with enforcement on. The pragma is a no-op inside a
-- transaction and ``defer_foreign_keys`` does not rescue it, because RESTRICT
-- is immediate.

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
            'red_team_unstaffed',
            'no_capable_agent'
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

-- The unroutable sweep pages ``WHERE status = 'blocked' AND blocked_reason =
-- ...`` every pass. That query returned nothing while the CHECK refused the
-- value, so its cost was never paid; it returns rows from here on and would
-- otherwise scan every task in the archive to find the handful that are parked.
-- Status leads because it is the more selective of the two and the pair is how
-- every caller asks; ``id`` trails because that sweep pages by keyset (``id >
-- <last>`` under the query's ``ORDER BY id``), so with it the walk is one index
-- range scan and without it every page re-sorts the matching rows.
CREATE INDEX idx_tasks_status_blocked_reason ON tasks (status, blocked_reason, id);
