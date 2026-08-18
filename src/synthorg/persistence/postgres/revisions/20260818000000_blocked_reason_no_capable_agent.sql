-- transactional: false
-- A park the loop performs and the database refuses.
--
-- ``BlockedReason.NO_CAPABLE_AGENT`` names the outcome of routing finding
-- nobody the work could go to: no agent the stakes admit, at any rung, scored
-- above the floor. The work is still wanted and the row is still good, so the
-- task parks and waits on an operator rather than failing. One writer persists
-- it (``engine/coordination/service.py``); ``engine/review_staffing/
-- reconciler.py`` logs it and ``engine/review_staffing/unroutable.py`` selects
-- on it, so both depend on the write landing without performing it. The public
-- ``Task`` DTO has carried the member in its enum since it was added.
--
-- The CHECK did not list it, so every one of those parks violated the
-- constraint on write: the insert raised, the task never reached BLOCKED, and
-- the reason an operator needed to see (hire, re-bind a model, or revise the
-- plan item) was the one reason the archive could not hold.
--
-- This migration runs outside a transaction because ``tasks`` is the busiest
-- table in the system and both statements below would otherwise hold their
-- locks on it until commit. A plain ``ADD CONSTRAINT ... CHECK`` takes ACCESS
-- EXCLUSIVE for a full validating pass, and a plain ``CREATE INDEX`` blocks
-- every write for the whole build. Neither is a cost this change needs to
-- impose: the widening admits values the constraint previously refused, so no
-- existing row can fail it, and the index is new.
--
-- Running outside a transaction means a failure part way through leaves the
-- earlier statements applied, so every statement here is re-runnable. The
-- constraint is swapped in ONE statement rather than dropped and re-added
-- across two, so there is never a window where the table has no reason
-- constraint at all; the index is dropped before it is built, because a
-- previous run that failed mid-build leaves an INVALID index behind that
-- CONCURRENTLY cannot reuse.

-- NOT VALID skips the up-front scan and takes only a brief lock. VALIDATE then
-- takes SHARE UPDATE EXCLUSIVE, which does not block concurrent writes, and
-- cannot fail: every row already satisfies a predicate that only gained values.
ALTER TABLE tasks
DROP CONSTRAINT IF EXISTS tasks_blocked_reason_check,
ADD CONSTRAINT tasks_blocked_reason_check CHECK (
    blocked_reason IN (
        'oracle_escalated',
        'wave_released',
        'reviewer_unstaffed',
        'red_team_unstaffed',
        'no_capable_agent'
    )
) NOT VALID;

ALTER TABLE tasks VALIDATE CONSTRAINT tasks_blocked_reason_check;

-- The unroutable sweep pages ``WHERE status = 'blocked' AND blocked_reason =
-- ...`` every pass. That query returned nothing while the CHECK refused the
-- value, so its cost was never paid; it returns rows from here on and would
-- otherwise scan every task in the archive to find the handful that are parked.
-- Status leads because it is the more selective of the two and the pair is how
-- every caller asks; ``id`` trails because that sweep pages by keyset (``id >
-- <last>`` under the query's ``ORDER BY id``), so with it the walk is one index
-- range scan and without it every page re-sorts the matching rows.
DROP INDEX CONCURRENTLY IF EXISTS idx_tasks_status_blocked_reason;
CREATE INDEX CONCURRENTLY idx_tasks_status_blocked_reason
ON tasks (status, blocked_reason, id);
