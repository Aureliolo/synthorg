-- Company-grade plan review: extend the durable Plan with the review-surface
-- fields and add a per-item comment thread.
--
-- objective_title denormalises the objective's human title onto the plan so the
-- review surface never resolves (or falls back to) a raw id, and carries the
-- same non-blank CHECK as its sibling id columns. review holds the consolidated
-- stakeholder-panel review (JSON, null until reviewed). open_questions /
-- assumptions are JSON string arrays the owner surfaces for the human.
-- objective_criteria denormalises the objective's acceptance criteria onto the
-- plan so the coverage map can flag any criterion no item advances, without
-- resolving the parent task. version_history is a JSON array of prior-version
-- snapshots, for diffing. plan_item_comments is an async discussion thread keyed
-- by (plan_id, item_id), written independently of the version-guarded plan row so
-- a comment never conflicts with a plan rework.
--
-- SQLite cannot ADD a NOT NULL + non-blank-CHECK column to a populated table (the
-- transient default would fail the CHECK), so the review-surface columns are
-- added by rebuilding the table into its final shape, backfilling objective_title
-- from the objective id for any pre-existing plan.

CREATE TABLE plans_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (LENGTH(TRIM(project)) > 0),
    objective_id TEXT NOT NULL CHECK (LENGTH(TRIM(objective_id)) > 0),
    objective_title TEXT NOT NULL CHECK (LENGTH(TRIM(objective_title)) > 0),
    parent_task_id TEXT NOT NULL CHECK (LENGTH(TRIM(parent_task_id)) > 0),
    items TEXT NOT NULL
    CHECK (JSON_VALID(items) AND JSON_TYPE(items) = 'array' AND JSON_ARRAY_LENGTH(items) > 0),
    task_structure TEXT NOT NULL DEFAULT 'sequential',
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'pending_review', 'approved', 'rejected', 'superseded')),
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
    task_structure, coordination_topology, status, forecast_id, review,
    open_questions, assumptions, objective_criteria, version_history, version,
    created_at, updated_at
)
SELECT
    id,
    project,
    objective_id,
    objective_id AS objective_title,
    parent_task_id,
    items,
    task_structure,
    coordination_topology,
    status,
    forecast_id,
    NULL AS review,
    '[]' AS open_questions,
    '[]' AS assumptions,
    '[]' AS objective_criteria,
    '[]' AS version_history,
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

CREATE TABLE plan_item_comments (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    plan_id TEXT NOT NULL CHECK (LENGTH(TRIM(plan_id)) > 0),
    item_id TEXT NOT NULL CHECK (LENGTH(TRIM(item_id)) > 0),
    author TEXT NOT NULL CHECK (LENGTH(TRIM(author)) > 0),
    body TEXT NOT NULL CHECK (LENGTH(TRIM(body)) > 0),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_plan_item_comments_plan_item
ON plan_item_comments (plan_id, item_id, created_at);

-- Optimistic concurrency for the Project aggregate: a version column so a
-- staffing write (stamping a project's lead) cannot silently clobber a
-- concurrent update from another worker process. DEFAULT 1 satisfies the CHECK
-- for every existing row, so the column adds cleanly to a populated table.
ALTER TABLE projects ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);
