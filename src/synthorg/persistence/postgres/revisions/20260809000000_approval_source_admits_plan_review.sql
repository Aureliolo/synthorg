-- The approvals table refused the one source the plan-review gate writes.
--
-- ``ApprovalSource`` has five members; ``approvals_source_check`` admitted
-- four. ``plan_review`` was never in the list, so every plan reaching human
-- review failed to persist its approval. The gap was invisible for as long
-- as it existed because no approval reached the database at all: the store
-- was constructed without a repository, held its queue in memory, and the
-- CHECK was never evaluated. The first run with durable approvals hit it
-- immediately, and the plan was failed nine milliseconds after reaching
-- PENDING_REVIEW, with no approval for an operator to decide.
--
-- Widening rather than dropping: the list is the schema's statement of what
-- the column may hold, and the fix is to make that statement true, not to
-- stop making it. ``check_sql_enum_check_constraints.py`` now holds every
-- such list to its Python enum, so the next member added to
-- ``ApprovalSource`` fails the gate rather than the run.
--
-- Runs in yoyo's default transaction. Re-adding a CHECK requires a full
-- table scan to validate existing rows; every stored value is one of the
-- four already admitted, so the scan cannot fail, and the table is small
-- (it held no rows at all until the revision before this one).

ALTER TABLE approvals DROP CONSTRAINT approvals_source_check;

ALTER TABLE approvals ADD CONSTRAINT approvals_source_check CHECK (
    source IN (
        'parked_context', 'review_gate',
        'conversational_intake', 'conversational_invite',
        'plan_review'
    )
);
