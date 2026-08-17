-- A park the loop performs and the database refuses.
--
-- ``BlockedReason.NO_CAPABLE_AGENT`` names the outcome of routing finding
-- nobody the work could go to: no agent the stakes admit, at any rung, scored
-- above the floor. The work is still wanted and the row is still good, so the
-- task parks and waits on an operator rather than failing. Three writers stamp
-- it -- ``engine/coordination/service.py``, ``engine/review_staffing/
-- reconciler.py`` and ``engine/review_staffing/unroutable.py`` -- and the
-- public ``Task`` DTO has carried it in its enum since it was added.
--
-- The CHECK never learned about it. The revision that introduced the member
-- shipped other tables and left this constraint at the four values
-- 20260814000000 gave it, so every one of those parks violated the CHECK on
-- write: the insert raised, the task never reached BLOCKED, and the reason an
-- operator needed to see (hire, re-bind a model, or revise the plan item) was
-- the one reason the archive could not hold.
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
