"""The shared teardown window that keeps the tail of shutdown reachable.

Per-service stop budgets are each individually sane, run in series, and sum to
far more than any container's termination grace period. On a slow stop SIGKILL
therefore arrives mid-sequence, and the steps that lose are the ones at the
end, which are exactly the steps that persist state. These cover the clamp that
keeps the sequence reaching them.
"""

import asyncio
from typing import Final

import pytest

from synthorg.api.lifecycle_shared import (
    _EXHAUSTED_STEP_FLOOR_SECONDS,
    _FLOOR_RESERVE_SECONDS,
    _MIN_STEP_GRANT_SECONDS,
    _try_stop,
    _windowed_timeout,
    shutdown_window,
)
from tests._shared import FakeClock

#: The longest per-service budget the teardown carries, so a clamp is visible.
_STEP_BUDGET: Final[float] = 32.0
#: Matches ``_TOTAL_SHUTDOWN_WINDOW_SECONDS``; the value is what the arithmetic
#: is asserted against, not what is under test, so it is stated locally.
_WINDOW: Final[float] = 75.0
#: A budget well under the window, to prove the window never widens a step.
_SHORT_STEP_BUDGET: Final[float] = 2.0
#: How far short of the window's end a partial-spend case stops.
_HEADROOM: Final[float] = 5.0
#: Multiplier that takes the clock comfortably past the window's end.
_OVERSHOOT: Final[int] = 2
#: More draws than the reserve can fund, so exhaustion is reached.
_DRAWS_PAST_RESERVE: Final[int] = (
    int(_FLOOR_RESERVE_SECONDS / _EXHAUSTED_STEP_FLOOR_SECONDS) + 4
)
#: The teardown's real step count, which is what makes a per-step floor unsafe.
_TEARDOWN_STEPS: Final[int] = 33
#: Slack over the reserve for event-loop scheduling in the wall-clock assertion.
_ELAPSED_ALLOWANCE_SECONDS: Final[float] = 3.0


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
            clock.advance(_WINDOW - _HEADROOM)

            assert _windowed_timeout(_STEP_BUDGET) == pytest.approx(_HEADROOM)

    def test_a_smaller_step_budget_survives_the_window(self) -> None:
        """The window is a ceiling, never a grant: it never widens a step."""
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            assert _windowed_timeout(_SHORT_STEP_BUDGET) == pytest.approx(
                _SHORT_STEP_BUDGET
            )

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
            clock.advance(_WINDOW * _OVERSHOOT)

            assert _windowed_timeout(_STEP_BUDGET) == _EXHAUSTED_STEP_FLOOR_SECONDS

    def test_the_floor_is_drawn_from_a_bounded_reserve(self) -> None:
        """A per-step floor overruns by however many steps happen to remain.

        The teardown has 33 of them, so half a second each is past the margin
        the grace period leaves. The reserve makes the overrun a property of
        one constant instead of the service count.
        """
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            clock.advance(_WINDOW * _OVERSHOOT)
            granted = [
                _windowed_timeout(_STEP_BUDGET) or 0.0
                for _ in range(_DRAWS_PAST_RESERVE)
            ]

        drawn_from_reserve = sum(
            grant for grant in granted if grant > _MIN_STEP_GRANT_SECONDS
        )
        assert drawn_from_reserve == pytest.approx(_FLOOR_RESERVE_SECONDS)
        # Past the reserve a step gets the minimum, never zero: zero would not
        # start the coroutine at all, which skips the tail rather than
        # shortening it.
        assert granted[-1] == pytest.approx(_MIN_STEP_GRANT_SECONDS)

    def test_the_whole_teardown_cannot_overrun_past_the_reserve(self) -> None:
        """The property a grace period is actually sized against."""
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW

        with shutdown_window(lambda: deadline - clock.monotonic()):
            clock.advance(_WINDOW * _OVERSHOOT)
            overrun = sum(
                _windowed_timeout(_STEP_BUDGET) or 0.0 for _ in range(_TEARDOWN_STEPS)
            )

        ceiling = _FLOOR_RESERVE_SECONDS + _MIN_STEP_GRANT_SECONDS * _TEARDOWN_STEPS
        assert overrun <= ceiling

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
            clock.advance(_WINDOW * _OVERSHOOT)
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
            clock.advance(_WINDOW * _OVERSHOOT)
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

    async def test_many_hung_tail_steps_stay_inside_the_reserve(self) -> None:
        """The case a per-step floor got wrong, driven through the real helper.

        Every remaining step hangs, so each one draws the floor. Bounded per
        step the sequence would overrun by the step count times the floor;
        bounded by the reserve it cannot exceed the reserve however many steps
        remain, which is what the grace period is sized against. The final
        step still completes, because the tail must be attempted.
        """
        loop = asyncio.get_running_loop()
        clock = FakeClock()
        deadline = clock.monotonic() + _WINDOW
        disconnected = False

        async def _hangs() -> None:
            await asyncio.Event().wait()

        async def _disconnect() -> None:
            nonlocal disconnected
            disconnected = True

        started = loop.time()
        with shutdown_window(lambda: deadline - clock.monotonic()):
            clock.advance(_WINDOW * _OVERSHOOT)
            for index in range(_TEARDOWN_STEPS):
                await _try_stop(
                    _hangs(),
                    "api.app.shutdown",
                    "hung",
                    timeout=_STEP_BUDGET,
                    service=f"wedged-{index}",
                )
            await _try_stop(
                _disconnect(),
                "api.app.shutdown",
                "disconnect failed",
                timeout=_STEP_BUDGET,
                service="persistence",
            )
        elapsed = loop.time() - started

        assert disconnected is True
        # Real wall-clock, since the hangs are awaited on the real loop. The
        # reserve is the ceiling; the allowance absorbs scheduling overhead.
        assert elapsed <= _FLOOR_RESERVE_SECONDS + _ELAPSED_ALLOWANCE_SECONDS
