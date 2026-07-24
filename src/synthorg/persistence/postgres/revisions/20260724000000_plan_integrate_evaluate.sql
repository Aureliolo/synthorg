-- The initiative tail: every plan item passing its own gate no longer completes
-- the plan, it opens INTEGRATING (the verified pieces are assembled into one
-- running deliverable and checked end to end) and then EVALUATING (that whole is
-- scored against the objective's success criteria). COMPLETED is reachable only
-- from EVALUATING, so the tail is structurally unskippable.
--
-- replan_generation counts how many times the auto-replan trigger has opened a
-- successor for this lineage, capping a runaway replan chain. A human replan
-- resets it to 0: a human decision is not a runaway.

ALTER TABLE plans DROP CONSTRAINT plans_status_check;
ALTER TABLE plans
ADD CONSTRAINT plans_status_check
CHECK (status IN (
    'planning', 'draft', 'pending_review', 'approved', 'executing',
    'integrating', 'evaluating', 'completed', 'rejected', 'superseded',
    'failed'
));

ALTER TABLE plans
ADD COLUMN replan_generation INTEGER NOT NULL DEFAULT 0
CHECK (replan_generation >= 0);
