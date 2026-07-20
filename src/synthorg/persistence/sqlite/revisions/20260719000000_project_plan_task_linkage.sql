-- Project / plan / task linkage: make a greenlit initiative one connected,
-- status-rolling graph.
--
--   1. projects.task_ids is dropped. It was write-orphaned (declared and
--      persisted, but never populated), and a stored collection of children
--      cannot be kept correct under concurrent writes. A project's tasks are
--      queried via tasks.project instead.
--   2. projects.plan_id names the one plan the project is currently
--      executing, repointed by the same write that supersedes a retired
--      revision. Earlier revisions stay reachable via plans.project.
--   3. tasks.plan_id / tasks.plan_item_id record which plan and which plan
--      item a dispatched task implements, so the correlation is stored data
--      rather than a re-derivation of the deterministic id mapping.
--   4. the plans status CHECK gains 'executing' and 'completed' so a plan can
--      express execution progress past approval. SQLite cannot ALTER a CHECK
--      in place, so the table is rebuilt.

ALTER TABLE projects DROP COLUMN task_ids;
ALTER TABLE projects ADD COLUMN plan_id TEXT;

ALTER TABLE tasks ADD COLUMN plan_id TEXT;
ALTER TABLE tasks ADD COLUMN plan_item_id TEXT;

CREATE INDEX idx_tasks_plan_id ON tasks (plan_id);

CREATE TABLE plans_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (LENGTH(TRIM(project)) > 0),
    objective_id TEXT NOT NULL CHECK (LENGTH(TRIM(objective_id)) > 0),
    objective_title TEXT NOT NULL CHECK (LENGTH(TRIM(objective_title)) > 0),
    parent_task_id TEXT NOT NULL CHECK (LENGTH(TRIM(parent_task_id)) > 0),
    items TEXT NOT NULL
    CHECK (
        JSON_VALID(items) AND JSON_TYPE(items) = 'array'
        AND (status IN ('planning', 'failed') OR JSON_ARRAY_LENGTH(items) > 0)
    ),
    task_structure TEXT NOT NULL DEFAULT 'sequential',
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN (
        'planning', 'draft', 'pending_review', 'approved', 'executing',
        'completed', 'rejected', 'superseded', 'failed'
    )),
    failure_reason TEXT CHECK (failure_reason IS NULL OR LENGTH(TRIM(failure_reason)) > 0),
    forecast_id TEXT,
    review TEXT,
    open_questions TEXT NOT NULL DEFAULT '[]',
    assumptions TEXT NOT NULL DEFAULT '[]',
    objective_criteria TEXT NOT NULL DEFAULT '[]',
    version_history TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- failure_reason is present iff the plan is FAILED: a FAILED plan must carry
    -- a reason (so Plan Review always shows why), and no other status may carry
    -- one. Mirrors the Plan model validator as the persistence-level backstop.
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
);

INSERT INTO plans_new (
    id, project, objective_id, objective_title, parent_task_id, items,
    task_structure, coordination_topology, status, failure_reason, forecast_id,
    review, open_questions, assumptions, objective_criteria, version_history,
    version, created_at, updated_at
)
SELECT
    id,
    project,
    objective_id,
    objective_title,
    parent_task_id,
    items,
    task_structure,
    coordination_topology,
    status,
    failure_reason,
    forecast_id,
    review,
    open_questions,
    assumptions,
    objective_criteria,
    version_history,
    version,
    created_at,
    updated_at
FROM plans;

DROP TABLE plans;
ALTER TABLE plans_new RENAME TO plans;

CREATE INDEX idx_plans_status ON plans (status);
CREATE INDEX idx_plans_project ON plans (project);
CREATE INDEX idx_plans_objective ON plans (objective_id);
CREATE INDEX idx_plans_project_status ON plans (project, status, id);
