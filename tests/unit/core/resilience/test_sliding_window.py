"""Tests for the core sliding-window event limiter and its builder."""

import asyncio

import pytest

from synthorg.core.resilience import (
    SlidingWindowEventLimiter,
    build_revalidation_limiter,
)
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class TestSlidingWindowEventLimiterRaceConditions:
    """Race-condition regression tests.

    Concurrent ``take()`` / ``release()`` must not admit beyond
    ``max_events`` and must keep the per-key timestamp lists
    internally consistent. The ``asyncio.Lock`` guarantees two awaiting
    tasks cannot both observe ``len(events) < max_events`` and admit
    beyond budget.
    """

    async def test_concurrent_take_admits_at_most_max_events(self) -> None:
        max_events = 3
        n_callers = 50
        fake_clock = FakeClock()
        limiter = SlidingWindowEventLimiter(
            max_events=max_events,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )
        barrier = asyncio.Barrier(n_callers)

        async def attempt_take() -> object | None:
            await barrier.wait()
            return await limiter.take("agent-A")

        # CLAUDE.md async-concurrency rule: prefer ``asyncio.TaskGroup``
        # for fan-out/fan-in. Each task body is the ``attempt_take``
        # helper; the limiter's lock guarantees per-call atomicity, so
        # no task can raise an unrelated error that would unwind the
        # group.
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(attempt_take()) for _ in range(n_callers)]
        results = [task.result() for task in tasks]
        # The non-None handles are the granted admissions.
        granted = [r for r in results if r is not None]
        assert len(granted) == max_events

    async def test_release_after_concurrent_take_reopens_slots(self) -> None:
        max_events = 5
        fake_clock = FakeClock()
        limiter = SlidingWindowEventLimiter(
            max_events=max_events,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )

        # Saturate the window. Capture each admission handle so we
        # can release the exact slots later.
        handles: list[object] = []
        for _ in range(max_events):
            admission = await limiter.take("agent-A")
            assert admission is not None
            handles.append(admission)
        assert await limiter.take("agent-A") is None

        # Release all admissions concurrently using their handles.
        # ``asyncio.TaskGroup`` per CLAUDE.md async-concurrency rule;
        # the bodies are infallible (limiter.release returns None on
        # missing-handle), so no task can unwind the group.
        async with asyncio.TaskGroup() as tg:
            for handle in handles:
                _ = tg.create_task(limiter.release("agent-A", handle))

        # All slots should be free again.
        n_callers = 20
        barrier = asyncio.Barrier(n_callers)

        async def attempt_take() -> object | None:
            await barrier.wait()
            return await limiter.take("agent-A")

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(attempt_take()) for _ in range(n_callers)]
        results = [task.result() for task in tasks]
        granted = [r for r in results if r is not None]
        assert len(granted) == max_events

    async def test_release_targets_exact_admission_not_newest(self) -> None:
        """``release`` refunds the exact admission, not the newest.

        Two same-agent admissions are alive concurrently. The OLDER
        one fails after the NEWER one has already been granted; a
        ``release`` that pops the newest timestamp would refund
        the *newer* slot, leaving the failed admission counted and
        squatting on capacity.

        With ``max_events=2`` we hold two live handles at the same
        time, release the FIRST one, then assert the limiter knows
        exactly one slot reopened (a third take returns a fresh
        handle that is neither of the originals; a fourth take is
        rejected because the second admission still holds its slot).
        Setting ``max_events=1`` cannot reproduce the bug -- only one
        handle is ever live, so any release happens to land on the
        right slot by luck.
        """
        max_events = 2
        fake_clock = FakeClock()
        limiter = SlidingWindowEventLimiter(
            max_events=max_events,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )
        # Two same-agent admissions in-flight concurrently.
        first = await limiter.take("agent-A")
        assert first is not None
        second = await limiter.take("agent-A")
        assert second is not None
        assert second is not first
        # Window is saturated.
        assert await limiter.take("agent-A") is None

        # Release the FIRST (older) admission by handle. Under the
        # old buggy ``release`` (pop newest), this would have removed
        # ``second`` instead.
        await limiter.release("agent-A", first)

        # Exactly one slot must be free: a third take succeeds with
        # a brand-new handle.
        third = await limiter.take("agent-A")
        assert third is not None
        assert third is not first
        assert third is not second
        # The second admission is still occupying its slot, so a
        # fourth take is rejected. If the buggy refund-newest logic
        # were still in place, ``second`` would have been popped
        # earlier and this take would unexpectedly succeed.
        assert await limiter.take("agent-A") is None

        # Final disambiguation: explicitly release ``second`` and
        # confirm a slot reopens. Under the buggy "pop newest"
        # release, ``second`` would already have been popped during
        # the earlier ``release(first)`` call, so this second
        # release would be a no-op and the next take would still
        # return None. Under the correct "remove this exact handle"
        # logic, ``second`` is still in the events table and the
        # release frees its slot, so a fresh handle is granted.
        await limiter.release("agent-A", second)
        fourth = await limiter.take("agent-A")
        assert fourth is not None
        assert fourth is not first
        assert fourth is not second
        assert fourth is not third


class TestBuildRevalidationLimiter:
    """The tick-aware window clamp shared by the SSE and WS loops."""

    def test_clamps_window_to_span_max_failures_ticks(self) -> None:
        # A 60s nominal window with a 600s tick interval and 5 failures
        # would never saturate (each failure ages out before the next
        # tick). The builder must widen the window to interval * failures.
        limiter = build_revalidation_limiter(
            max_failures=5,
            window_seconds=60.0,
            interval_seconds=600.0,
        )
        assert limiter._window_seconds == 600.0 * 5
        assert limiter._max_events == 5

    def test_keeps_window_when_already_wide_enough(self) -> None:
        # When the nominal window already spans the failure ticks, it
        # is kept as-is (no spurious widening).
        limiter = build_revalidation_limiter(
            max_failures=2,
            window_seconds=10_000.0,
            interval_seconds=600.0,
        )
        assert limiter._window_seconds == 10_000.0
