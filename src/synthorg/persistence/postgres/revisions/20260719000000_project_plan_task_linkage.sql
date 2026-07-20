-- transactional: false
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
--
-- This migration runs outside a transaction so the plan index can be built
-- CONCURRENTLY. tasks is the busiest table in the system, and a plain CREATE
-- INDEX would hold a SHARE lock on it for the whole build; wrapped in one
-- transaction with the plans CHECK rebuild below, it would hold that lock
-- alongside an ACCESS EXCLUSIVE lock on plans until commit.
--
-- Running outside a transaction means a mid-migration failure leaves the
-- earlier statements applied, so each change below is expressed as ONE
-- ALTER TABLE. Every statement is then individually atomic, and in particular
-- the plans CHECK is swapped in a single statement rather than dropped and
-- re-added across two: there is never a window where the table has no status
-- constraint.

ALTER TABLE projects
DROP COLUMN task_ids,
ADD COLUMN plan_id TEXT;

ALTER TABLE tasks
ADD COLUMN plan_id TEXT,
ADD COLUMN plan_item_id TEXT;

ALTER TABLE plans
DROP CONSTRAINT plans_status_check,
ADD CONSTRAINT plans_status_check
CHECK (status IN (
    'planning', 'draft', 'pending_review', 'approved', 'executing',
    'completed', 'rejected', 'superseded', 'failed'
));

CREATE INDEX CONCURRENTLY idx_tasks_plan_id ON tasks (plan_id);
