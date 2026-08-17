-- The project's human name is denormalised onto the plan.
--
--
-- Every plan surface (the review inbox row, the detail header, the chat card
-- that links to it) had only plans.project, which is a project id. An id is a
-- database key, not information: it is not memorable, not comparable by eye,
-- and it crowds out the name it stands in for. This is the same denormalisation
-- objective_title already carries, for the same stated reason -- so the surface
-- never has to resolve an id, and never falls back to showing one.
--
-- SQLite cannot add a NOT NULL column carrying a CHECK and then drop the
-- transient default, so the table is rebuilt into its final shape, backfilled
-- from projects, and its five indices recreated.

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
    planning_strategy TEXT
    CHECK (planning_strategy IS NULL OR LENGTH(TRIM(planning_strategy)) > 0),
    review_absent_reason TEXT
    CHECK (
        review_absent_reason IS NULL
        OR LENGTH(TRIM(review_absent_reason)) > 0
    ),
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
);

-- COALESCE, not a join that drops rows, so the migration stays total. The
-- fallback is a word rather than the id: the column is what a surface prints,
-- and an id printed under the heading "project" is the defect this column was
-- added to remove. Nothing is lost by not repeating it, since plans.project
-- still carries the key.
INSERT INTO plans_new (
    id, project, project_name, objective_id, objective_title, parent_task_id,
    items, task_structure, coordination_topology, status, failure_reason,
    forecast_id, review, open_questions, assumptions, objective_criteria,
    version_history, replan_generation, version, created_at, updated_at,
    planning_strategy, review_absent_reason
)
SELECT
    plans.id,
    plans.project,
    COALESCE(
        (
            SELECT projects.name FROM projects
            WHERE projects.id = plans.project
        ),
        'Unknown project'
    ) AS project_name,
    plans.objective_id,
    plans.objective_title,
    plans.parent_task_id,
    plans.items,
    plans.task_structure,
    plans.coordination_topology,
    plans.status,
    plans.failure_reason,
    plans.forecast_id,
    plans.review,
    plans.open_questions,
    plans.assumptions,
    plans.objective_criteria,
    plans.version_history,
    plans.replan_generation,
    plans.version,
    plans.created_at,
    plans.updated_at,
    plans.planning_strategy,
    plans.review_absent_reason
FROM plans;

DROP TABLE plans;
ALTER TABLE plans_new RENAME TO plans;

CREATE INDEX idx_plans_status ON plans (status);
CREATE INDEX idx_plans_project ON plans (project);
CREATE INDEX idx_plans_objective ON plans (objective_id);
CREATE INDEX idx_plans_project_status ON plans (project, status, id);
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id, id);
