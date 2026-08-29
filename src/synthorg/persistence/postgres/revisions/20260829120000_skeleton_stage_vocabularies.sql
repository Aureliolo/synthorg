-- transactional: false
-- Two vocabularies the skeleton stage produces and the database would refuse.
--
-- ``PlanStatus.SKELETON`` is the head stage: the contract becomes code, as
-- module layout, one pending test per acceptance criterion and the project's
-- gate configuration, before any unit builds against it. Approval now writes
-- this status rather than EXECUTING, so without the widening every approved
-- plan would violate the constraint on its very first write, the plan would
-- keep its old status, and an operator would see an approved plan with nothing
-- running under it and nothing saying why.
--
-- This runs outside a transaction so the constraint swap can take the
-- short-lock path: NOT VALID and VALIDATE only avoid holding ACCESS EXCLUSIVE
-- over a full scan of ``plans`` if they commit separately, and inside one
-- transaction the validation would hold its lock until the end anyway.
--
-- Running outside a transaction means a failure part way through leaves the
-- earlier statements applied, so every statement here is re-runnable.

-- Swapped in ONE statement rather than dropped and re-added across two, so
-- there is never a window where ``plans`` has no status constraint at all.
--
-- NOT VALID skips the up-front scan and takes only a brief lock; the separate
-- VALIDATE below then takes SHARE UPDATE EXCLUSIVE, which does not block
-- concurrent writes. That cost buys nothing here anyway: the change only ADDS a
-- value to the predicate, so no existing row can fail it and the validation
-- cannot fail either.
ALTER TABLE plans
DROP CONSTRAINT IF EXISTS plans_status_check,
ADD CONSTRAINT plans_status_check CHECK (
    status IN (
        'planning',
        'draft',
        'pending_review',
        'approved',
        'skeleton',
        'executing',
        'integrating',
        'evaluating',
        'completed',
        'rejected',
        'superseded',
        'failed'
    )
) NOT VALID;

ALTER TABLE plans VALIDATE CONSTRAINT plans_status_check;

-- The gate configuration the skeleton commits declares how a project lints,
-- formats and checks its dependencies, and the oracle requires a passing
-- recorded run of each. Those runs are captured under their own purpose, so
-- without the widening every one of them would violate this constraint, the
-- receipt would be swallowed by the capture's best-effort handler, and the
-- oracle would block the unit for evidence that was produced and refused.
ALTER TABLE code_execution_record
DROP CONSTRAINT IF EXISTS code_execution_record_purpose_check,
ADD CONSTRAINT code_execution_record_purpose_check CHECK (
    purpose IN ('general', 'tests', 'lint', 'format', 'dependency')
) NOT VALID;

ALTER TABLE code_execution_record
VALIDATE CONSTRAINT code_execution_record_purpose_check;
