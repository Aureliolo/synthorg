-- Plan first-class from greenlight: a PLANNING shell is persisted the moment an
-- objective is greenlit (before decomposition fills its items), and a FAILED
-- plan records a decomposition that never produced items, so a failed run
-- always leaves a visible plan carrying failure_reason instead of a silent
-- orphan. Postgres can alter the inline CHECKs in place (no table rebuild):
--   1. status CHECK gains 'planning' and 'failed'.
--   2. the items CHECK permits an empty array for the itemless statuses
--      ('planning' shell not yet filled, 'failed' never filled).
--   3. a nullable failure_reason column (non-blank when present, and set iff the
--      status is 'failed') surfaces why a FAILED plan failed on the review
--      surface.

ALTER TABLE plans DROP CONSTRAINT plans_items_check;
ALTER TABLE plans
ADD CONSTRAINT plans_items_check
CHECK (
    JSONB_TYPEOF(items) = 'array'
    AND (status IN ('planning', 'failed') OR JSONB_ARRAY_LENGTH(items) > 0)
);

ALTER TABLE plans DROP CONSTRAINT plans_status_check;
ALTER TABLE plans
ADD CONSTRAINT plans_status_check
CHECK (status IN (
    'planning', 'draft', 'pending_review', 'approved', 'rejected',
    'superseded', 'failed'
));

ALTER TABLE plans
ADD COLUMN failure_reason TEXT
CHECK (failure_reason IS NULL OR CHAR_LENGTH(TRIM(failure_reason)) > 0);

-- failure_reason is present iff the plan is FAILED: a FAILED plan must carry a
-- reason (so Plan Review always shows why), and no other status may carry one.
-- Mirrors the Plan model validator as the persistence-level backstop.
ALTER TABLE plans
ADD CONSTRAINT plans_failure_reason_status_check
CHECK ((status = 'failed') = (failure_reason IS NOT NULL));
