-- Two blocked reasons the CHECK constraint never admitted.
--
-- 1. ``no_capable_agent`` shipped in ``BlockedReason`` and is written by
--    production code (``engine/coordination/service.py`` when routing finds
--    nobody, and ``engine/review_staffing/unroutable.py``), but was never
--    added to either backend's CHECK. Every such park therefore failed its
--    write, leaving the subtask in whatever status it already held: on the
--    run that surfaced this, two subtasks sat at ``created``, undispatched,
--    with nothing watching them and no exit.
--
-- 2. ``dependency_failed`` is new. A wave is now gated on whether the work
--    its subtasks declared they depend on actually delivered; one whose
--    inputs died parks under this reason instead of dispatching against
--    outputs nobody wrote. It is kept apart from ``wave_released`` because
--    the two wait on different things: a released subtask waits on a
--    scheduler, and this one waits on its dependency being redone, which
--    only a replan can order.
--
-- SQLite cannot alter a column CHECK in place, so the table is rebuilt into
-- its final shape, copied across, and its four indices recreated.

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
            'no_capable_agent',
            'dependency_failed'
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
    id, title, description, type, priority, project, plan_id, plan_item_id,
    created_by, requested_by_user_id, assigned_to, status,
    estimated_complexity, budget_limit, deadline, max_retries, parent_task_id,
    task_structure, coordination_topology, reviewers, dependencies,
    artifacts_expected, acceptance_criteria, delegation_chain, hard_ceiling,
    forecast_id, source, middleware_override, metadata, hard_token_ceiling,
    blocked_reason
FROM tasks;

DROP TABLE tasks;
ALTER TABLE tasks_new RENAME TO tasks;

CREATE INDEX idx_tasks_status ON tasks (status);
CREATE INDEX idx_tasks_assigned_to ON tasks (assigned_to);
CREATE INDEX idx_tasks_project ON tasks (project);
CREATE INDEX idx_tasks_plan_id ON tasks (plan_id);
