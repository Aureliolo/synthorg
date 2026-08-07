-- A plan's parent task and a comment's plan become real references, and a
-- project records when it was opened.
--
-- ``plans.parent_task_id`` was a bare non-blank text check, so deleting a
-- task left its plan pointing at nothing. The orphan kept running:
-- decomposition completed against the deleted row, the plan reached
-- ``pending_review``, and it then could not be removed at all (project
-- delete tried to supersede it, which ``plans_items_check`` forbids while
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

-- Already-orphaned rows have to go before the references can hold. The
-- plans are unapprovable (their parent 404s), unsupersedable, and
-- undeletable, which is precisely the state this migration exists to make
-- unreachable; there is nothing to preserve in them.
--
-- Their dependents are deleted explicitly rather than left to
-- ``initiative_evaluation_report``'s own ON DELETE CASCADE. Postgres would
-- fire it, but yoyo runs the SQLite arm with ``foreign_keys`` at its OFF
-- default, so there the cascade never fires and the reports survive
-- pointing at dropped ids. Doing it by hand is the only way both arms end
-- in the same state.
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

ALTER TABLE plans
ADD CONSTRAINT plans_parent_task_id_fkey
FOREIGN KEY (parent_task_id) REFERENCES tasks (id) ON DELETE RESTRICT;

ALTER TABLE plan_item_comments
ADD CONSTRAINT plan_item_comments_plan_id_fkey
FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE CASCADE;

-- The task-delete guard reads `WHERE parent_task_id = ? ORDER BY id
-- LIMIT 1`, so `id` rides the index: equality first, then the ordering.
-- Unindexed it is a sequential scan of the plans table per deletion.
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id, id);

-- The same rationale, applied to the two sibling columns that reference
-- ``tasks`` with nothing indexing them. ``decision_records`` already leads
-- an index with ``task_id`` and needs none.
CREATE INDEX idx_tm_task_id ON task_metrics (task_id);

-- Existing projects predate the column, so their start was never recorded.
-- The workspace is provisioned with the project and the first plan is
-- drafted for it, so either one dates it better than a guess. A project
-- with neither is stamped at the epoch rather than at migration time: "now"
-- would place every pre-existing project inside the reuse window it is
-- about to gain, which is the opposite of what an unknown age should mean.
ALTER TABLE projects ADD COLUMN created_at TIMESTAMPTZ;
ALTER TABLE projects ADD COLUMN updated_at TIMESTAMPTZ;

UPDATE projects
SET created_at = COALESCE(
    (
        -- MIN, matching the plans arm below: a project may hold several
        -- workspace rows, and an unaggregated scalar subquery aborts the
        -- whole migration here while SQLite silently takes an arbitrary
        -- one, so the two backends would disagree on the same input.
        SELECT MIN(w.created_at) FROM project_workspaces AS w
        WHERE w.project_id = projects.id
    ),
    (
        SELECT MIN(p.created_at) FROM plans AS p
        WHERE p.project = projects.id
    ),
    TIMESTAMPTZ '1970-01-01 00:00:00+00'
);

UPDATE projects SET updated_at = created_at;

ALTER TABLE projects ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE projects ALTER COLUMN updated_at SET NOT NULL;

CREATE INDEX idx_projects_created_at ON projects (created_at);
