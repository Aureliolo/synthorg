"""TOCTOU race tests for MeetingScheduler.start.

``MeetingScheduler.start()`` at ``communication/meeting/scheduler.py``
(pre-fix, lines 109-148) performs an unsynchronized ``_running``
check-and-set and then spawns a periodic task per scheduled meeting
type. Two concurrent ``start()`` calls both passing the check would
double-spawn every periodic task. The fix wraps the body in a
dedicated ``_lifecycle_lock`` so exactly one caller wins.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from synthorg.communication.config import MeetingsConfig, MeetingTypeConfig
from synthorg.communication.meeting.errors import SchedulerAlreadyRunningError
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import ParticipantResolver
from synthorg.communication.meeting.scheduler import MeetingScheduler


def _build_config() -> MeetingsConfig:
    """Build a MeetingsConfig with two periodic meeting types.

    A single scheduled type is enough to check the race, but two
    periodic types make the duplicate-task count easier to read.
    """
    return MeetingsConfig(
        enabled=True,
        types=(
            MeetingTypeConfig(
                name="daily_standup",
                frequency=MeetingFrequency.DAILY,
                participants=("alice", "bob"),
            ),
            MeetingTypeConfig(
                name="weekly_retro",
                frequency=MeetingFrequency.WEEKLY,
                participants=("alice", "bob"),
            ),
        ),
    )


@pytest.mark.unit
class TestConcurrentStart:
    """Concurrent ``start()`` calls must yield exactly one success."""

    async def test_concurrent_start_spawns_one_task_per_type(self) -> None:
        """Invariants at the end of ``asyncio.gather(start(), start())``:

        - exactly one success + one ``SchedulerAlreadyRunningError``
        - ``scheduler._tasks`` has exactly ``len(scheduled_types)`` tasks
          (not 2x)
        """
        config = _build_config()
        scheduler = MeetingScheduler(
            config=config,
            orchestrator=MagicMock(spec=MeetingOrchestrator),
            participant_resolver=MagicMock(spec=ParticipantResolver),
        )

        # Force both ``start()`` callers into the lifecycle-lock
        # critical section concurrently via a barrier on ``acquire()``.
        # Without this the sync check-and-set on ``_running`` runs in
        # one scheduler turn and the second caller sees the flag
        # already flipped -- which *also* produces exactly one success
        # and one failure, but does not exercise the contention window
        # the lock is there to defend. The barrier guarantees both
        # callers attempt ``acquire()`` before either wins, so a
        # regression that removes the lock would allow both to proceed
        # past the check before either commits the set.
        #
        # The barrier only applies to the first two ``acquire()`` calls
        # (the two concurrent ``start()`` invocations). Subsequent
        # calls -- specifically the ``stop()`` in ``finally`` -- skip
        # the barrier so the cleanup path does not deadlock on a
        # barrier waiting for a second party that will never arrive.
        barrier = asyncio.Barrier(2)
        acquire_count = 0

        class _CoordinatedLock(asyncio.Lock):
            async def acquire(self) -> bool:  # type: ignore[override]
                nonlocal acquire_count
                acquire_count += 1
                if acquire_count <= 2:
                    await barrier.wait()
                return await super().acquire()

        scheduler._lifecycle_lock = _CoordinatedLock()

        results = await asyncio.gather(
            scheduler.start(),
            scheduler.start(),
            return_exceptions=True,
        )
        try:
            successes = [r for r in results if not isinstance(r, BaseException)]
            failures = [
                r for r in results if isinstance(r, SchedulerAlreadyRunningError)
            ]
            assert len(successes) == 1, f"expected one success, got {results!r}"
            assert len(failures) == 1, (
                f"expected one SchedulerAlreadyRunningError, got {results!r}"
            )
            scheduled_types = scheduler.get_scheduled_types()
            assert len(scheduler._tasks) == len(scheduled_types), (
                f"expected {len(scheduled_types)} tasks, got {len(scheduler._tasks)}"
            )
        finally:
            await scheduler.stop()
