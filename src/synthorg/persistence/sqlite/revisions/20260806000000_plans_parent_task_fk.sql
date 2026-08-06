-- A plan's parent task becomes a real reference.
--
-- ``parent_task_id`` was a bare non-blank text check, so deleting a task
-- left its plan pointing at nothing. The orphan kept running: decomposition
-- completed against the deleted row, the plan reached ``pending_review``,
-- and it then could not be removed at all (project delete tried to supersede
-- it, which the items CHECK forbids while ``items`` is empty).
--
-- RESTRICT rather than CASCADE. A plan is a reviewed decision record, and
-- its evaluation reports already cascade off it, so a task delete under
-- CASCADE would silently destroy a plan, its review, and its delivery
-- verdicts behind a 204. Refusing the delete puts the choice back with the
-- operator, who resolves the plan first via DELETE /plans/{id}.
--
-- SQLite cannot add a constraint to an existing table, so the table is
-- rebuilt (create-new, copy, drop, rename) and its four indexes recreated.
-- ``initiative_evaluation_report`` references plans (id), which is
-- unaffected: the rebuild preserves every surviving id.

-- Already-orphaned rows have to go before the reference can hold. They are
-- unapprovable (their parent 404s), unsupersedable, and undeletable, which
-- is precisely the state this migration exists to make unreachable; there is
-- nothing to preserve in them.
DELETE FROM plans
WHERE NOT EXISTS (
    SELECT 1 FROM tasks WHERE tasks.id = plans.parent_task_id
);

CREATE TABLE plans_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (LENGTH(TRIM(project)) > 0),
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
        'planning', 'draft', 'pending_review', 'approved', 'executing',
        'integrating', 'evaluating', 'completed', 'rejected', 'superseded',
        'failed'
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
    -- failure_reason is present iff the plan is FAILED: a FAILED plan must carry
    -- a reason (so Plan Review always shows why), and no other status may carry
    -- one. Mirrors the Plan model validator as the persistence-level backstop.
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
);

INSERT INTO plans_new (
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
    replan_generation,
    version,
    created_at,
    updated_at
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
    replan_generation,
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
-- The reference is read on every task delete and on the orphan check, and
-- unindexed it is a full scan of the plans table per deletion.
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id);
