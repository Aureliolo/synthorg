"""The shared teardown window that keeps the tail of shutdown reachable.

Per-service stop budgets are each individually sane, run in series, and sum to
far more than any container's termination grace period. On a slow stop SIGKILL
therefore arrives mid-sequence, and the steps that lose are the ones at the
end, which are exactly the steps that persist state. These cover the clamp that
keeps the sequence reaching them.
"""

import asyncio

import pytest

from synthorg.api.lifecycle_shared import (
    _EXHAUSTED_STEP_FLOOR_SECONDS,
    _try_stop,
    _windowed_timeout,
    shutdown_window,
)
from tests._shared import FakeClock

_STEP_BUDGET = 32.0
_WINDOW = 75.0


@pytest.mark.unit
class TestWindowedTimeout:
    """A step gets the smaller of its own budget and what the window has left."""

    def test_no_window_leaves_the_step_budget_alone(self) -> None:
        """Startup-failure cleanup shares the helper and opens no window."""
        assert _windowed_timeout(_STEP_BUDGET) == _STEP_BUDGET
        assert _windowed_timeout(None) is None

    def test_the_window_clamps_a_larger_step_budget(self) -> None:
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            clock.advance(_WINDOW - 5.0)

            assert _windowed_timeout(_STEP_BUDGET) == pytest.approx(5.0)

    def test_a_smaller_step_budget_survives_the_window(self) -> None:
        """The window is a ceiling, never a grant: it never widens a step."""
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            assert _windowed_timeout(2.0) == pytest.approx(2.0)

    def test_an_unbounded_step_becomes_bounded_inside_the_window(self) -> None:
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            assert _windowed_timeout(None) == pytest.approx(_WINDOW)

    def test_a_spent_window_still_grants_the_floor(self) -> None:
        """Zero is the arithmetically honest answer and the wrong one.

        The steps at the end of the teardown persist state, so they must be
        attempted even when the services ahead of them ran the window down.
        """
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            clock.advance(_WINDOW * 2)

            assert _windowed_timeout(_STEP_BUDGET) == _EXHAUSTED_STEP_FLOOR_SECONDS

    def test_the_window_is_released_on_exit(self) -> None:
        """A window must not leak past the teardown that opened it."""
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            pass

        assert _windowed_timeout(_STEP_BUDGET) == _STEP_BUDGET


@pytest.mark.unit
class TestTailStepsStillRun:
    """The point of the clamp: the last steps are reached, not skipped."""

    async def test_a_step_past_the_window_is_attempted_not_skipped(self) -> None:
        """A spent window shortens the tail step; it never drops it.

        The audit-chain flush and the persistence disconnect sit at the end of
        the sequence, so "ran out of budget" must still mean the coroutine was
        awaited.
        """
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW
        ran = False

        async def _flush() -> None:
            nonlocal ran
            ran = True

        with shutdown_window(lambda: deadline - clock.monotonic()):
            clock.advance(_WINDOW * 2)
            stopped = await _try_stop(
                _flush(),
                "api.app.shutdown",
                "flush failed",
                timeout=_STEP_BUDGET,
                service="audit_chain",
            )

        assert ran is True
        assert stopped is True

    async def test_a_hung_step_does_not_consume_a_later_step(self) -> None:
        """One service hanging must cost its own budget, not the sequence."""
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW
        tail_ran = False

        async def _hangs() -> None:
            await asyncio.Event().wait()

        async def _tail() -> None:
            nonlocal tail_ran
            tail_ran = True

        with shutdown_window(lambda: deadline - clock.monotonic()):
            # Spend the window, so the hung step is clamped to the floor and
            # the real event loop reaches its timeout without a long wait.
            clock.advance(_WINDOW * 2)
            hung_stopped = await _try_stop(
                _hangs(),
                "api.app.shutdown",
                "hung",
                timeout=_STEP_BUDGET,
                service="wedged",
            )
            await _try_stop(
                _tail(),
                "api.app.shutdown",
                "tail failed",
                timeout=_STEP_BUDGET,
                service="persistence",
            )

        assert hung_stopped is False
        assert tail_ran is True
