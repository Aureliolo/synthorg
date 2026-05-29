# mypy: disable-error-code="explicit-any"
"""TOCTOU race tests for PerformanceTracker.

Each test is written to fail reliably against the un-synchronized
implementation and pass once ``_metrics_lock`` is applied to the
relevant mutator / reader / setter.

Three race sites are covered:

1. ``clear()`` / ``aclear()`` racing concurrent ``record_task_metric``
   calls -- without the lock the clear can observe a partially-updated
   ``_task_metrics`` dict and mutators can observe a mid-clear state.
2. ``get_collaboration_score()`` racing ``record_collaboration_event``:
   without the lock, ``list(self._collab_metrics[agent_id])`` can
   read an appended-mid-iteration list.
3. ``set_inflection_sink()`` as an atomic "set once" operation:
   without the lock, two concurrent setters both observe ``None`` and
   both succeed, silently overwriting the first.

The tests rely on explicit ``await asyncio.sleep(0)`` yield points
inside helper coroutines to force deterministic interleaving so the
race is reproducible without probabilistic timing.
"""

import asyncio
from typing import Literal, override

import pytest

from synthorg.hr.performance.tracker import PerformanceTracker

from .conftest import make_collab_metric, make_task_metric

_AGENT_ID = "agent-race-001"


@pytest.mark.unit
class TestClearConcurrentWithRecord:
    """``aclear()`` must atomically reset state vs concurrent recorders."""

    async def test_aclear_is_atomic_against_record_task_metric(self) -> None:
        """Concurrent records + aclear must not raise or lose structural invariants.

        Under the un-synchronized ``clear()`` the bare ``dict.clear()``
        called while ``record_task_metric`` is appending can raise
        ``RuntimeError: dictionary changed size during iteration`` in
        adjacent readers, or leave the append dangling on a freshly
        cleared key. Post-aclear invariants:

        - no exceptions were raised
        - either the aclear won cleanly (dict empty) or a record won
          cleanly (dict has the recorded entry), never a torn state
        """
        tracker = PerformanceTracker()
        n_records = 100
        errors: list[BaseException] = []
        acquires = 0

        # Swap in an instrumented lock that counts acquires so we can
        # assert the lock path was actually exercised. If a future
        # refactor removes ``_metrics_lock``, ``acquires`` stays at 0
        # and the assertion fails -- without this, the test would
        # false-pass because ``record_task_metric`` / ``aclear`` have
        # no internal ``await`` between their read and write steps,
        # so they serialize naturally on any single-threaded event
        # loop even with the lock removed.
        class _CountingLock(asyncio.Lock):
            @override
            async def acquire(self) -> bool:  # type: ignore[override]
                nonlocal acquires
                acquires += 1
                return await super().acquire()

        tracker._metrics_lock = _CountingLock()

        async def _record(i: int) -> None:
            # Explicit yield so records interleave with the clear
            # scheduled mid-loop: TaskGroup.create_task schedules but
            # does not run until the next loop pass, and without this
            # sleep(0) the records could run back-to-back before the
            # clear task ever starts, defeating the race.
            await asyncio.sleep(0)
            try:
                await tracker.record_task_metric(
                    make_task_metric(
                        agent_id=_AGENT_ID,
                        task_id=f"task-{i}",
                    ),
                )
            except Exception as exc:
                errors.append(exc)

        async def _clear() -> None:
            # Same deterministic yield so the clear contests the lock
            # at the same scheduling point as the records around it.
            await asyncio.sleep(0)
            try:
                await tracker.aclear()
            except Exception as exc:
                errors.append(exc)

        async with asyncio.TaskGroup() as tg:
            for i in range(n_records):
                _ = tg.create_task(_record(i))
                if i == n_records // 2:
                    _ = tg.create_task(_clear())

        # Both record_task_metric and aclear MUST acquire
        # ``_metrics_lock`` -- if they don't, the test is vacuous and
        # a regression that drops the lock would slip through. With
        # 100 records plus one clear we expect > 100 acquires.
        assert acquires > n_records, (
            f"expected > {n_records} metrics-lock acquires "
            f"(record + clear both under lock); got {acquires}"
        )

        # The specific race symptom we're guarding against is
        # ``RuntimeError: dictionary changed size during iteration`` /
        # ``RuntimeError: set changed size during iteration``;
        # assert NO RuntimeError of that shape leaks, not just "no
        # errors at all", so a future regressor that raises a
        # different exception type doesn't hide the race.
        dict_iteration_errors = [
            e
            for e in errors
            if isinstance(e, RuntimeError) and "changed size during iteration" in str(e)
        ]
        assert dict_iteration_errors == [], (
            f"dict/set iteration race leaked: {dict_iteration_errors!r}"
        )
        assert errors == [], f"unexpected exceptions: {errors!r}"
        # Structural invariant: every entry in ``_task_metrics`` is a list
        # of records for that agent id. An interleaved clear-then-append
        # is fine -- a torn append is not.
        for agent_key, records in tracker._task_metrics.items():
            assert isinstance(records, list)
            for record in records:
                assert str(record.agent_id) == agent_key


@pytest.mark.unit
class TestGetCollaborationScoreLocking:
    """``get_collaboration_score()`` must acquire ``_metrics_lock`` for the snapshot.

    In today's single-threaded asyncio runtime, the un-synchronized
    ``tuple(self._collab_metrics.get(str(agent_id), []))`` in
    ``get_collaboration_score`` cannot tear because the scheduler
    only interleaves at ``await`` points. The lock is still required
    as a correctness contract so that future refactors cannot
    introduce an ``await`` between the dict read and the tuple
    snapshot without tripping the lock. (Line numbers intentionally
    omitted -- they drift with every edit to ``tracker.py``.)

    This test asserts the observable contract: at least one acquire
    happens on the shared lock during ``get_collaboration_score``.
    Concurrent-writer exclusion is enforced by the shared
    ``_metrics_lock`` (a single ``asyncio.Lock``) and is covered by
    the aclear / rng-swap race tests above, not duplicated here.
    """

    async def test_get_score_acquires_metrics_lock(self) -> None:
        """The snapshot path must take ``_metrics_lock`` at least once."""
        tracker = PerformanceTracker()
        # Pre-seed one record so the strategy has something to score.
        await tracker.record_collaboration_event(
            make_collab_metric(agent_id=_AGENT_ID),
        )

        # Count acquires on the tracker's lock for the duration of the
        # score call by subclassing ``asyncio.Lock`` so ``async with``
        # semantics and typeshed annotations remain unchanged.
        acquires = 0

        class _CountingLock(asyncio.Lock):
            @override
            async def acquire(self) -> Literal[True]:
                nonlocal acquires
                acquires += 1
                return await super().acquire()

        tracker._metrics_lock = _CountingLock()
        await tracker.get_collaboration_score(_AGENT_ID)

        assert acquires >= 1, (
            "get_collaboration_score must acquire _metrics_lock for the snapshot"
        )


@pytest.mark.unit
class TestSetInflectionSinkAtomic:
    """``set_inflection_sink()`` must be a serialized set-once operation."""

    async def test_concurrent_setters_race_produces_exactly_one_success(self) -> None:
        """Two concurrent set calls must yield exactly one success + one ValueError.

        Under the un-synchronized property setter both callers can pass
        the ``self._inflection_sink is not None and value is not None``
        guard when the field is ``None`` and both can assign -- the
        second write silently overwrites the first with no error raised.
        The async ``set_inflection_sink`` implementation takes
        ``_metrics_lock`` so exactly one caller wins.

        Determinism: the controllable yield is injected at the
        ``_metrics_lock.acquire()`` boundary itself by swapping in a
        custom ``asyncio.Lock`` subclass whose ``acquire()`` rendezvous
        both callers at a ``Barrier(2)`` before delegating to the real
        acquire. This proves the test is *actually* exercising the
        lock path: if a future refactor removes the lock but keeps the
        setter await-free, ``acquire()`` is never called, the barrier
        never fills, both callers deadlock on ``barrier.wait()``, and
        the test fails loudly instead of silently false-passing.

        Two distinct sentinel sink objects are used so the assertion
        can verify *which* caller won, not just that a sink exists.
        """
        tracker = PerformanceTracker()
        sink_a: object = object()
        sink_b: object = object()

        barrier = asyncio.Barrier(2)
        acquires = 0

        class _CoordinatedLock(asyncio.Lock):
            """Lock whose acquire() rendezvous at a barrier first.

            Counts acquire calls so the test can assert the lock path
            was actually exercised -- if set_inflection_sink ever
            becomes lock-free, acquires stays at 0 and the assertion
            fails.
            """

            @override
            async def acquire(self) -> bool:  # type: ignore[override]
                nonlocal acquires
                acquires += 1
                await barrier.wait()
                return await super().acquire()

        tracker._metrics_lock = _CoordinatedLock()

        results = await asyncio.gather(
            tracker.set_inflection_sink(sink_a),  # type: ignore[arg-type]
            tracker.set_inflection_sink(sink_b),  # type: ignore[arg-type]
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, ValueError)]
        assert len(successes) == 1, f"expected exactly one success, got {successes!r}"
        assert len(failures) == 1, f"expected exactly one ValueError, got {failures!r}"
        # The lock's acquire() path must have run -- guards against a
        # future refactor that removes the lock.
        assert acquires >= 1, "set_inflection_sink must acquire _metrics_lock"
        # Exactly one of the two candidate sinks is installed.
        assert tracker.inflection_sink in {sink_a, sink_b}
