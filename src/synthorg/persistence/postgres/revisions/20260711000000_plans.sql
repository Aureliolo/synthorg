-- Durable Plan entity: the reviewable, revisable breakdown of an objective
-- into ordered, ownable items. Replaces the transient DecompositionResult that
-- previously lived only inside an approval's metadata. Backs the /plans API and
-- the Plan Review workspace; a plan-review approval references its plan_id.
-- items is a JSON array of plan-item objects (id, title, description,
-- dependencies, owner, acceptance_criteria, expected_artifacts,
-- estimated_complexity, stakes). created_at / updated_at are ISO-8601 UTC
-- strings.

CREATE TABLE plans (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    project TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(project)) > 0),
    objective_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(objective_id)) > 0),
    parent_task_id TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(parent_task_id)) > 0),
    items JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(items) = 'array'),
    task_structure TEXT NOT NULL DEFAULT 'sequential',
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'pending_review', 'approved', 'rejected', 'superseded')),
    forecast_id TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_plans_status ON plans (status);
CREATE INDEX idx_plans_project ON plans (project);
CREATE INDEX idx_plans_objective ON plans (objective_id);
