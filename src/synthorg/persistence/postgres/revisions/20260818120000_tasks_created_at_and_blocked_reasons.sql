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
-- 2. ``dependency_failed``. A wave is gated on whether the work its subtasks
--    declared they depend on actually delivered; one whose inputs died parks
--    under this reason instead of dispatching against outputs nobody wrote.
--    It is kept apart from ``wave_released`` because the two wait on
--    different things: a released subtask waits on a scheduler, and this one
--    waits on its dependency being redone, which only a replan can order.
--
-- 3. ``run_stopped`` is the honest complement of ``dependency_failed``. An
--    execution group is one round of AGENTS, not one level of the DAG: a
--    level whose subtasks share an agent is split across several groups. So
--    the groups after the one a run stopped at include SIBLINGS of it, whose
--    declared inputs are untouched. Both park, because a row left at
--    ``created`` has no exit; only the ones actually below the stop are a
--    dependency failure, and the rest say they merely never started.

-- ``IF NOT EXISTS`` keeps the add re-runnable. yoyo keys applied revisions on
-- the migration id rather than on content, so a database that already carries
-- the column reads as not having run this one and gets the whole file again;
-- without the guard it fails on the first statement, and the constraint swap
-- below, which is the half that decides whether the loop can park a task at
-- all, never runs. The widening is the same either way.
ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

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
        'dependency_failed',
        'run_stopped'
    )
);
