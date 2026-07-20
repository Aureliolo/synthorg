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
--      express execution progress past approval.

ALTER TABLE projects DROP COLUMN task_ids;
ALTER TABLE projects ADD COLUMN plan_id TEXT;

ALTER TABLE tasks ADD COLUMN plan_id TEXT;
ALTER TABLE tasks ADD COLUMN plan_item_id TEXT;

CREATE INDEX idx_tasks_plan_id ON tasks (plan_id);

ALTER TABLE plans DROP CONSTRAINT plans_status_check;
ALTER TABLE plans
ADD CONSTRAINT plans_status_check
CHECK (status IN (
    'planning', 'draft', 'pending_review', 'approved', 'executing',
    'completed', 'rejected', 'superseded', 'failed'
));
