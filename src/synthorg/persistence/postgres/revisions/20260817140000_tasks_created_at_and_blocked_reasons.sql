-- Two task-table changes: a creation timestamp, and two blocked reasons the
-- CHECK constraint never admitted. One revision per backend per PR, so both
-- deltas ride together.
--
-- 1. ``tasks.created_at``. The loop's central entity recorded no creation
--    time, so "how long has this been running" had no durable answer: the
--    duration histogram measured from an in-process map seeded on create and
--    lost on restart, the cockpit could not age out a row nothing had driven
--    since a restart, and a restart is exactly when the question gets asked.
--
--    Pre-existing rows are backfilled with this migration's own run time.
--    Nothing in either schema records when they were filed, so that value is
--    an UPPER BOUND, not their real creation time: a legacy task reads as no
--    older than the migration. Every row filed after this reads exactly.
--
-- 2. ``no_capable_agent`` shipped in ``BlockedReason`` and is written by
--    production code (``engine/coordination/service.py`` when routing finds
--    nobody, and ``engine/review_staffing/unroutable.py``), but was never
--    added to either backend's CHECK. Every such park therefore failed its
--    write, leaving the subtask in whatever status it already held: on the
--    run that surfaced this, two subtasks sat at ``created``, undispatched,
--    with nothing watching them and no exit.
--
-- 3. ``dependency_failed`` is new. A wave is now gated on whether the work
--    its subtasks declared they depend on actually delivered; one whose
--    inputs died parks under this reason instead of dispatching against
--    outputs nobody wrote. It is kept apart from ``wave_released`` because
--    the two wait on different things: a released subtask waits on a
--    scheduler, and this one waits on its dependency being redone, which
--    only a replan can order.

ALTER TABLE tasks
ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE tasks
ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE tasks
DROP CONSTRAINT tasks_blocked_reason_check;

ALTER TABLE tasks
ADD CONSTRAINT tasks_blocked_reason_check CHECK (
    blocked_reason IN (
        'oracle_escalated',
        'wave_released',
        'reviewer_unstaffed',
        'red_team_unstaffed',
        'no_capable_agent',
        'dependency_failed'
    )
);
