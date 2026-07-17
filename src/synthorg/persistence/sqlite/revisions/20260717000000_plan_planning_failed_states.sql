-- Plan first-class from greenlight: a PLANNING shell is persisted the moment an
-- objective is greenlit (before decomposition fills its items), and a FAILED
-- plan records a decomposition that never produced items, so a failed run
-- always leaves a visible plan carrying failure_reason instead of a silent
-- orphan. This requires three changes to the plans table, all CHECK / column
-- shape changes SQLite cannot ALTER in place, so the table is rebuilt:
--   1. status CHECK gains 'planning' and 'failed'.
--   2. the items CHECK permits an empty array for the itemless statuses
--      ('planning' shell not yet filled, 'failed' never filled).
--   3. a nullable failure_reason column (non-blank when present) surfaces why a
--      FAILED plan failed on the review surface.

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
        'planning', 'draft', 'pending_review', 'approved', 'rejected',
        'superseded', 'failed'
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
    updated_at TEXT NOT NULL
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
    NULL AS failure_reason,
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
