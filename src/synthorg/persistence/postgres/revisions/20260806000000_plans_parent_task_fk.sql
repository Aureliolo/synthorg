-- A plan's parent task becomes a real reference.
--
-- ``parent_task_id`` was a bare non-blank text check, so deleting a task
-- left its plan pointing at nothing. The orphan kept running: decomposition
-- completed against the deleted row, the plan reached ``pending_review``,
-- and it then could not be removed at all (project delete tried to supersede
-- it, which ``plans_items_check`` forbids while ``items`` is empty).
--
-- RESTRICT rather than CASCADE. A plan is a reviewed decision record, and
-- its evaluation reports already cascade off it, so a task delete under
-- CASCADE would silently destroy a plan, its review, and its delivery
-- verdicts behind a 204. Refusing the delete puts the choice back with the
-- operator, who resolves the plan first via DELETE /plans/{id}.
--
-- Already-orphaned rows have to go before the constraint can be added.
-- They are unapprovable (their parent 404s), unsupersedable, and
-- undeletable, which is precisely the state this migration exists to make
-- unreachable; there is nothing to preserve in them.
DELETE FROM plans
WHERE NOT EXISTS (
    SELECT 1 FROM tasks WHERE tasks.id = plans.parent_task_id
);

ALTER TABLE plans
ADD CONSTRAINT plans_parent_task_id_fkey
FOREIGN KEY (parent_task_id) REFERENCES tasks (id) ON DELETE RESTRICT;

-- The reference is read on every task delete and on the orphan check, and
-- unindexed it is a sequential scan of the plans table per deletion.
CREATE INDEX idx_plans_parent_task ON plans (parent_task_id);
