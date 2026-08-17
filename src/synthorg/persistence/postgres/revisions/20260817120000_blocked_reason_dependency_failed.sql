-- Two blocked reasons the CHECK constraint never admitted.
--
-- 1. ``no_capable_agent`` shipped in ``BlockedReason`` and is written by
--    production code (``engine/coordination/service.py`` when routing finds
--    nobody, and ``engine/review_staffing/unroutable.py``), but was never
--    added to either backend's CHECK. Every such park therefore failed its
--    write, leaving the subtask in whatever status it already held: on the
--    run that surfaced this, two subtasks sat at ``created``, undispatched,
--    with nothing watching them and no exit.
--
-- 2. ``dependency_failed`` is new. A wave is now gated on whether the work
--    its subtasks declared they depend on actually delivered; one whose
--    inputs died parks under this reason instead of dispatching against
--    outputs nobody wrote. It is kept apart from ``wave_released`` because
--    the two wait on different things: a released subtask waits on a
--    scheduler, and this one waits on its dependency being redone, which
--    only a replan can order.

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
