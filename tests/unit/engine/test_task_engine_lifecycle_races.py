"""TOCTOU race tests for TaskEngine start/stop.

The un-synchronized ``start()`` at ``engine/task_engine.py:125`` did
not acquire ``_lifecycle_lock`` even though ``stop()`` did. Two paths
can race:

1. Two concurrent ``start()`` calls both observe ``_running=False``
   and both create duplicate ``_processing_task`` / ``_observer_task``
   instances, leaking the first set.
2. A concurrent ``start()`` during ``stop()``'s drain: stop flips
   ``_running=False`` under the lock, releases, then starts awaiting
   the drain -- but the racing ``start()`` now sees ``_running=False``
   and spawns a *new* processing task, which stop never drains.

The fix holds ``_lifecycle_lock`` across the full start body and the
full stop body (including drains) so the two lifecycle transitions
are mutually exclusive.
"""

import asyncio
from typing import override

import pytest

from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from tests.unit.engine.task_engine_helpers import FakePersistence


@pytest.mark.unit
class TestConcurrentStart:
    """Concurrent ``start()`` calls must be serialized via ``_lifecycle_lock``."""

    async def test_concurrent_start_spawns_single_task_set(self) -> None:
        """Exactly one ``start()`` wins; the loser raises ``RuntimeError``.

        Invariants at the end of the gather:

        - exactly one success + one ``RuntimeError``
        - exactly one ``task-engine-loop`` task exists on the event loop
        - exactly one ``task-engine-observer-dispatcher`` task exists
        """
        persistence: FakePersistence = FakePersistence()
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]

        # Barrier-coordinated lock so both ``start()`` callers reach
        # the lifecycle-lock acquire concurrently; otherwise scheduler
        # ordering can let one start() finish before the other
        # contends and the test would pass on an un-locked
        # implementation. Only the first two acquires barrier-wait;
        # the ``stop()`` call in ``finally`` uses a third acquire that
        # must proceed without waiting for a second party.
        barrier = asyncio.Barrier(2)
        acquire_count = 0

        class _CoordinatedLock(asyncio.Lock):
            @override
            async def acquire(self) -> bool:  # type: ignore[override]
                nonlocal acquire_count
                acquire_count += 1
                if acquire_count <= 2:
                    await barrier.wait()
                return await super().acquire()

        eng._lifecycle_lock = _CoordinatedLock()

        results = await asyncio.gather(
            eng.start(),
            eng.start(),
            return_exceptions=True,
        )
        try:
            successes = [r for r in results if not isinstance(r, BaseException)]
            failures = [r for r in results if isinstance(r, RuntimeError)]
            assert len(successes) == 1, f"expected one success, got {results!r}"
            assert len(failures) == 1, f"expected one RuntimeError, got {results!r}"

            loop_tasks = [
                t for t in asyncio.all_tasks() if t.get_name() == "task-engine-loop"
            ]
            assert len(loop_tasks) == 1, (
                f"expected 1 task-engine-loop, got {len(loop_tasks)}"
            )
            observer_tasks = [
                t
                for t in asyncio.all_tasks()
                if t.get_name() == "task-engine-observer-dispatcher"
            ]
            assert len(observer_tasks) == 1, (
                f"expected 1 observer task, got {len(observer_tasks)}"
            )
        finally:
            await eng.stop(timeout=2.0)


@pytest.mark.unit
class TestStartDuringStop:
    """``start()`` racing an in-flight ``stop()`` must be serialized.

    Without the stop-holds-the-lock fix, this interleaving race:
    - stop flips _running=False, releases lifecycle lock, awaits drain
    - concurrent start sees _running=False, sets True, spawns new tasks
    - stop resumes drain against a now-stale self._processing_task
      reference -- the new processing task is never awaited, leaks

    With the fix (stop holds _lifecycle_lock across drains), the
    second start must wait until stop fully completes and the lock
    releases.
    """

    async def test_start_waits_for_stop_to_fully_complete(self) -> None:
        persistence: FakePersistence = FakePersistence()
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        await eng.start()

        # Deterministic ordering: fire stop() first and block inside
        # its drain until the test has scheduled start(). Without this
        # sentinel-Event pattern, ``asyncio.gather(stop, start)`` can
        # schedule start() before stop() acquires _lifecycle_lock; in
        # that case start() would legitimately raise "already running"
        # and the test asserts the wrong contract (scheduling order,
        # not lifecycle serialisation).
        stop_holding_lock = asyncio.Event()
        start_may_proceed = asyncio.Event()

        original_drain = eng._drain_all

        async def gated_drain(effective_timeout: float) -> None:
            # Signal the test loop from inside stop()'s drain (which
            # is called under _lifecycle_lock), then block until the
            # test has scheduled the racing start().
            stop_holding_lock.set()
            await start_may_proceed.wait()
            await original_drain(effective_timeout)

        eng._drain_all = gated_drain  # type: ignore[method-assign]

        stop_task = asyncio.create_task(eng.stop(timeout=2.0))
        await stop_holding_lock.wait()
        # start() is scheduled while stop() is provably holding the
        # lifecycle lock mid-drain. It MUST block on the lock until
        # stop() finishes.
        start_task = asyncio.create_task(eng.start())
        # Release the drain so stop() can complete.
        start_may_proceed.set()
        await stop_task
        await start_task
        try:
            # Invariant: there is exactly one processing loop + one
            # observer dispatcher task, not a leaked pair from a race.
            loop_tasks = [
                t for t in asyncio.all_tasks() if t.get_name() == "task-engine-loop"
            ]
            observer_tasks = [
                t
                for t in asyncio.all_tasks()
                if t.get_name() == "task-engine-observer-dispatcher"
            ]
            assert len(loop_tasks) == 1, (
                f"expected 1 task-engine-loop after start-during-stop, "
                f"got {len(loop_tasks)}"
            )
            assert len(observer_tasks) == 1, (
                f"expected 1 observer task after start-during-stop, "
                f"got {len(observer_tasks)}"
            )
        finally:
            await eng.stop(timeout=2.0)

    async def test_hard_deadline_releases_lifecycle_lock(self) -> None:
        """stop() must actually hit the hard-deadline branch and bail.

        A drain stage that blocks past the hard deadline must cause
        stop() to:
          1. Exceed the outer ``drain_timeout_seconds * 2`` deadline
          2. Re-raise ``TimeoutError`` to the caller
          3. Mark the engine unrestartable so a subsequent start()
             cannot attach a second loop pair on top of the orphaned
             first generation.

        We force the race by monkey-patching ``_drain_all`` with a
        coroutine that blocks on ``asyncio.Event().wait()``. The
        blocker is cancellable -- when ``asyncio.wait_for`` hits the
        hard deadline it cancels the inner task, which raises
        ``CancelledError``, which ``wait_for`` converts into the
        ``TimeoutError`` our ``stop()`` handles. A blocker that
        *swallows* cancellation would leave ``wait_for`` stuck waiting
        for cleanup, which is the production pathology the hard
        deadline is designed to bound -- but it is not necessary to
        simulate that to exercise the TimeoutError branch here.
        """
        persistence: FakePersistence = FakePersistence()
        # Tiny drain timeout so the test stays fast -- hard deadline
        # is 2x this, so the whole stop bounds to ~0.2s.
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=TaskEngineConfig(drain_timeout_seconds=0.05),
        )
        await eng.start()

        release = asyncio.Event()

        async def hanging_drain(_effective_timeout: float) -> None:
            # Block until cancelled by the outer wait_for's hard
            # deadline. Using Event().wait() (not sleep(large_number))
            # per CLAUDE.md guidance -- cancellation-safe, no timing
            # assumptions, and the outer wait_for's cancel is how we
            # reach the TimeoutError path.
            await release.wait()

        eng._drain_all = hanging_drain  # type: ignore[method-assign,assignment]

        # stop() must now raise TimeoutError from its asyncio.wait_for.
        with pytest.raises(TimeoutError):
            await eng.stop(timeout=0.05)

        # Engine must be marked unrestartable so a subsequent start()
        # cannot attach a second loop pair on top of orphaned tasks.
        assert eng._unrestartable is True

        # A racing start() after the timed-out stop must raise
        # RuntimeError -- we must not silently spawn a second
        # processing/observer pair on top of the orphaned first.
        with pytest.raises(RuntimeError, match="unrestartable"):
            await eng.start()

        # Release the blocker (no-op now since wait_for already
        # cancelled it), then cancel the background tasks directly.
        # stop() cannot recover the engine -- it's intentionally
        # unrestartable once marked -- so cleanup is manual.
        release.set()
        if eng._processing_task is not None:
            eng._processing_task.cancel()
        if eng._observer_task is not None:
            eng._observer_task.cancel()
        await asyncio.gather(
            *(t for t in (eng._processing_task, eng._observer_task) if t is not None),
            return_exceptions=True,
        )
