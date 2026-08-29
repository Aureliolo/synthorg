-- A lifecycle stage the loop performs and the database would refuse.
--
-- ``PlanStatus.SKELETON`` is the head stage: the contract becomes code, as
-- module layout, one pending test per acceptance criterion and the project's
-- gate configuration, before any unit builds against it. Approval now writes
-- this status rather than EXECUTING, so without the widening every approved
-- plan would violate the constraint on its very first write, the plan would
-- keep its old status, and an operator would see an approved plan with nothing
-- running under it and nothing saying why. That is the exact shape
-- 20260818000000 was written for on ``tasks``.
--
-- Widening a CHECK admits values it previously refused, so no existing row can
-- fail it and nothing is rewritten. SQLite cannot alter a CHECK, so ``plans``
-- is rebuilt (create-new, copy, drop, rename) and its indexes recreated, the
-- same shape 20260818000000 used.
--
-- This revision runs with foreign-key enforcement OFF, which is yoyo's default
-- and is load-bearing here: ``plans.parent_task_id`` references ``tasks`` ON
-- DELETE RESTRICT, and ``initiative_evaluation_report`` references ``plans``,
-- so a drop with enforcement on fails outright.

CREATE TABLE plans_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (LENGTH(TRIM(project)) > 0),
    project_name TEXT NOT NULL CHECK (LENGTH(TRIM(project_name)) > 0),
    objective_id TEXT NOT NULL CHECK (LENGTH(TRIM(objective_id)) > 0),
    objective_title TEXT NOT NULL CHECK (LENGTH(TRIM(objective_title)) > 0),
    parent_task_id TEXT NOT NULL
    REFERENCES tasks (id) ON DELETE RESTRICT
    CHECK (LENGTH(TRIM(parent_task_id)) > 0),
    items TEXT NOT NULL
    CHECK (
        JSON_VALID(items) AND JSON_TYPE(items) = 'array'
        AND (status IN ('planning', 'failed') OR JSON_ARRAY_LENGTH(items) > 0)
    ),
    task_structure TEXT NOT NULL DEFAULT 'sequential',
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN (
        'planning', 'draft', 'pending_review', 'approved', 'skeleton',
        'executing', 'integrating', 'evaluating', 'completed', 'rejected',
        'superseded', 'failed'
    )),
    failure_reason TEXT CHECK (failure_reason IS NULL OR LENGTH(TRIM(failure_reason)) > 0),
    forecast_id TEXT,
    review TEXT,
    open_questions TEXT NOT NULL DEFAULT '[]',
    assumptions TEXT NOT NULL DEFAULT '[]',
    objective_criteria TEXT NOT NULL DEFAULT '[]',
    version_history TEXT NOT NULL DEFAULT '[]',
    replan_generation INTEGER NOT NULL DEFAULT 0 CHECK (replan_generation >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    planning_strategy TEXT
    CHECK (planning_strategy IS NULL OR LENGTH(TRIM(planning_strategy)) > 0),
    review_absent_reason TEXT
    CHECK (
        review_absent_reason IS NULL
        OR LENGTH(TRIM(review_absent_reason)) > 0
    ),
    decomposition_progress TEXT
    CHECK (
        decomposition_progress IS NULL
        OR (
            JSON_VALID(decomposition_progress)
            AND JSON_TYPE(decomposition_progress) = 'object'
        )
    ),
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
);

INSERT INTO plans_new (
    id, project, project_name, objective_id, objective_title, parent_task_id,
    items, task_structure, coordination_topology, status, failure_reason,
    forecast_id, review, open_questions, assumptions, objective_criteria,
    version_history, replan_generation, version, created_at, updated_at,
    planning_strategy, review_absent_reason, decomposition_progress
)
SELECT
    id,
    project,
    project_name,
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
    replan_generation,
    version,
    created_at,
    updated_at,
    planning_strategy,
    review_absent_reason,
    decomposition_progress
FROM plans;

DROP TABLE plans;

ALTER TABLE plans_new RENAME TO plans;

CREATE INDEX idx_plans_status ON plans (status);
CREATE INDEX idx_plans_project ON plans (project);
CREATE INDEX idx_plans_objective ON plans (objective_id);
CREATE INDEX idx_plans_project_status ON plans (project, status, id);
-- The task-delete guard reads `WHERE parent_task_id = ? ORDER BY id LIMIT 1`,
-- so `id` rides the index: equality first, then the ordering.
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id, id);
