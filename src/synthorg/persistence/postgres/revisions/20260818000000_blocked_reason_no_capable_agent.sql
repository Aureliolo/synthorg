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
-- Widening a CHECK admits values it previously refused, so no existing row can
-- fail it and the re-add validates against the table without rewriting it.
-- Postgres can replace a named constraint in place, so unlike the SQLite twin
-- this needs no table rebuild.

ALTER TABLE tasks DROP CONSTRAINT tasks_blocked_reason_check;

ALTER TABLE tasks ADD CONSTRAINT tasks_blocked_reason_check CHECK (
    blocked_reason IN (
        'oracle_escalated',
        'wave_released',
        'reviewer_unstaffed',
        'red_team_unstaffed',
        'no_capable_agent'
    )
);

-- The unroutable sweep pages ``WHERE status = 'blocked' AND blocked_reason =
-- ...`` every pass. That query returned nothing while the CHECK refused the
-- value, so its cost was never paid; it returns rows from here on and would
-- otherwise scan every task in the archive to find the handful that are parked.
-- Status leads because it is the more selective of the two and the pair is how
-- every caller asks; ``id`` trails because that sweep pages by keyset (``id >
-- <last>`` under the query's ``ORDER BY id``), so with it the walk is one index
-- range scan and without it every page re-sorts the matching rows.
CREATE INDEX idx_tasks_status_blocked_reason ON tasks (status, blocked_reason, id);
