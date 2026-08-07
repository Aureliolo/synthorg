-- A plan's parent task and a comment's plan become real references, and a
-- project records when it was opened.
--
-- ``plans.parent_task_id`` was a bare non-blank text check, so deleting a
-- task left its plan pointing at nothing. The orphan kept running:
-- decomposition completed against the deleted row, the plan reached
-- ``pending_review``, and it then could not be removed at all (project
-- delete tried to supersede it, which the items CHECK forbids while
-- ``items`` is empty). ``plan_item_comments.plan_id`` was the same gap one
-- table down.
--
-- The two references differ because the rows differ. A plan is a reviewed
-- decision record with its own delivery verdicts hanging off it, so a task
-- delete RESTRICTs: destroying all of that as a side effect of removing a
-- task is a decision an operator should make deliberately, and
-- ``DELETE /plans/{id}`` is where they make it. A comment is a remark ON a
-- plan and means nothing without it, so it CASCADEs.
--
-- ``projects`` gains ``created_at`` / ``updated_at``. Intake bounds its
-- reuse of an existing project by age, and a project with no recorded start
-- has no age to bound.
--
-- SQLite cannot add a constraint or a NOT NULL column without a constant
-- default to an existing table, so all three tables are rebuilt
-- (create-new, copy, drop, rename) and their indexes recreated.

-- Already-orphaned rows have to go before the references can hold. The
-- plans are unapprovable (their parent 404s), unsupersedable, and
-- undeletable, which is precisely the state this migration exists to make
-- unreachable; there is nothing to preserve in them.
--
-- Their dependents are deleted explicitly rather than left to
-- ``initiative_evaluation_report``'s own ON DELETE CASCADE, because yoyo
-- runs with ``foreign_keys`` at its OFF default: the cascade never fires
-- here, and the reports would survive pointing at dropped ids, so the app
-- would then reconnect with ``foreign_keys = ON`` onto a live violation.
DELETE FROM initiative_evaluation_report
WHERE plan_id IN (
    SELECT id FROM plans
    WHERE NOT EXISTS (
        SELECT 1 FROM tasks
        WHERE tasks.id = plans.parent_task_id
    )
);

DELETE FROM plan_item_comments
WHERE plan_id IN (
    SELECT id FROM plans
    WHERE NOT EXISTS (
        SELECT 1 FROM tasks
        WHERE tasks.id = plans.parent_task_id
    )
);

DELETE FROM plans
WHERE NOT EXISTS (
    SELECT 1 FROM tasks
    WHERE tasks.id = plans.parent_task_id
);

-- A comment whose plan is already gone is orphaned by the same class of
-- bug and blocks the reference just as surely.
DELETE FROM plan_item_comments
WHERE NOT EXISTS (
    SELECT 1 FROM plans
    WHERE plans.id = plan_item_comments.plan_id
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
-- The task-delete guard reads `WHERE parent_task_id = ? ORDER BY id
-- LIMIT 1`, so `id` rides the index: equality first, then the ordering.
-- Unindexed it is a full scan of the plans table per deletion.
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id, id);

CREATE TABLE plan_item_comments_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    plan_id TEXT NOT NULL
    REFERENCES plans (id) ON DELETE CASCADE
    CHECK (LENGTH(TRIM(plan_id)) > 0),
    item_id TEXT NOT NULL CHECK (LENGTH(TRIM(item_id)) > 0),
    author TEXT NOT NULL CHECK (LENGTH(TRIM(author)) > 0),
    body TEXT NOT NULL CHECK (LENGTH(TRIM(body)) > 0),
    created_at TEXT NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'human'
    CHECK (author_kind IN ('human', 'agent')),
    author_agent_id TEXT
    CHECK (
        (author_agent_id IS NULL OR LENGTH(TRIM(author_agent_id)) > 0)
        AND ((author_kind = 'agent') = (author_agent_id IS NOT NULL))
    ),
    -- Names the post-rename table: the rebuild renames this table into
    -- place, and SQLite only rewrites references to the name being
    -- renamed FROM, so a self-reference has to be written as its final
    -- name to survive.
    reply_to_id TEXT
    REFERENCES plan_item_comments (id) ON DELETE SET NULL
    CHECK (reply_to_id IS NULL OR LENGTH(TRIM(reply_to_id)) > 0)
);

INSERT INTO plan_item_comments_new (
    id,
    plan_id,
    item_id,
    author,
    body,
    created_at,
    author_kind,
    author_agent_id,
    reply_to_id
)
SELECT
    id,
    plan_id,
    item_id,
    author,
    body,
    created_at,
    author_kind,
    author_agent_id,
    reply_to_id
FROM plan_item_comments;

DROP TABLE plan_item_comments;

ALTER TABLE plan_item_comments_new RENAME TO plan_item_comments;

CREATE INDEX idx_plan_item_comments_plan_item
ON plan_item_comments (plan_id, item_id, created_at);
CREATE INDEX idx_plan_item_comments_reply
ON plan_item_comments (reply_to_id)
WHERE reply_to_id IS NOT NULL;

-- The same rationale as the plans index, applied to the sibling column
-- that references ``tasks`` with nothing indexing it. ``decision_records``
-- already leads an index with ``task_id`` and needs none.
CREATE INDEX idx_tm_task_id ON task_metrics (task_id);

CREATE TABLE projects_new (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL DEFAULT '[]',
    lead TEXT,
    plan_id TEXT,
    deadline TEXT,
    budget REAL NOT NULL DEFAULT 0.0 CHECK (budget >= 0.0),
    status TEXT NOT NULL DEFAULT 'planning',
    autonomy_mode TEXT CHECK (autonomy_mode IN ('full', 'semi', 'supervised', 'locked')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Existing projects predate the columns, so their start was never
-- recorded. The workspace is provisioned with the project and the first
-- plan is drafted for it, so either one dates it better than a guess. A
-- project with neither is stamped at the epoch rather than at migration
-- time: "now" would place every pre-existing project inside the reuse
-- window it is about to gain, which is the opposite of what an unknown age
-- should mean.
INSERT INTO projects_new (
    id,
    name,
    description,
    team,
    lead,
    plan_id,
    deadline,
    budget,
    status,
    autonomy_mode,
    version,
    created_at,
    updated_at
)
SELECT
    p.id,
    p.name,
    p.description,
    p.team,
    p.lead,
    p.plan_id,
    p.deadline,
    p.budget,
    p.status,
    p.autonomy_mode,
    p.version,
    COALESCE(
        (
            SELECT w.created_at FROM project_workspaces AS w
            WHERE w.project_id = p.id
        ),
        (
            SELECT MIN(pl.created_at) FROM plans AS pl
            WHERE pl.project = p.id
        ),
        '1970-01-01T00:00:00+00:00'
    ),
    COALESCE(
        (
            SELECT w.created_at FROM project_workspaces AS w
            WHERE w.project_id = p.id
        ),
        (
            SELECT MIN(pl.created_at) FROM plans AS pl
            WHERE pl.project = p.id
        ),
        '1970-01-01T00:00:00+00:00'
    )
FROM projects AS p;

DROP TABLE projects;

ALTER TABLE projects_new RENAME TO projects;

CREATE INDEX idx_projects_status ON projects (status);
CREATE INDEX idx_projects_lead ON projects (lead);
-- Intake looks up a live project by age, so the ordering column is indexed.
CREATE INDEX idx_projects_created_at ON projects (created_at);
