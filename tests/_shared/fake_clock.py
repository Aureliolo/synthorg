"""Deterministic fake ``Clock`` implementation for tests.

Satisfies ``synthorg.core.clock.Clock`` structurally. The internal
``_now`` advances whenever ``sleep(seconds)`` or ``advance(seconds)``
is called -- no real waiting happens, so a 48-hour observation window
completes in microseconds. ``monotonic()`` is derived from the same
internal clock with a per-instance epoch fixed at construction, so the
returned float is monotonically non-decreasing across ``sleep`` and
``advance`` calls.

Use ``sleep`` in code under test (it records the requested duration in
``sleep_calls``) and ``advance`` in tests that want to move time
without recording a sleep call. ``advance_async`` yields control to
the event loop once after advancing, so cooperative tasks waiting on a
deadline get a chance to observe the new time.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta


def _validate_seconds(seconds: float, *, action: str) -> None:
    """Reject negative and non-finite (NaN / inf) durations.

    ``timedelta(seconds=nan)`` and ``timedelta(seconds=inf)`` produce
    nonsensical state that surfaces only later as opaque assertion
    failures; failing fast here keeps the test signal clean.
    """
    if not math.isfinite(seconds) or seconds < 0.0:
        msg = f"{action} seconds must be finite and non-negative, got {seconds}"
        raise ValueError(msg)


class FakeClock:
    """Virtual clock with manually-advanced time.

    Args:
        start: Initial wall-clock time. Must be timezone-aware.
            Defaults to ``2026-01-01 00:00:00 UTC``.
    """

    def __init__(self, *, start: datetime | None = None) -> None:
        if start is None:
            self._now: datetime = datetime(2026, 1, 1, tzinfo=UTC)
        else:
            if start.tzinfo is None or start.utcoffset() is None:
                msg = f"start must be timezone-aware; got naive datetime {start!r}"
                raise ValueError(msg)
            self._now = start.astimezone(UTC)
        # Per-instance monotonic origin: the value of ``_now`` at
        # construction. ``monotonic()`` returns ``(_now - _epoch).
        # total_seconds()``, which is non-decreasing because every
        # mutation only adds positive deltas.
        self._epoch: datetime = self._now
        self._sleep_calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """Advance the virtual clock by ``seconds`` without waiting.

        Records the requested duration in ``sleep_calls`` so tests can
        assert how long the code under test asked to sleep, then yields
        control to the event loop once so cancellation on the awaiting
        task surfaces the same way it would under ``SystemClock.sleep``.
        Without that single ``asyncio.sleep(0)`` a cancelled timer
        task could exit FakeClock.sleep "synchronously" and a test
        using FakeClock-driven timers would silently miss cancellation
        propagation that the production code relies on.

        Args:
            seconds: Finite non-negative duration.

        Raises:
            ValueError: If ``seconds`` is negative or non-finite (NaN / inf).
        """
        _validate_seconds(seconds, action="sleep")
        # Yield BEFORE mutating state so a cancelled awaiter exits
        # without the FakeClock recording the (cancelled) sleep
        # duration or advancing virtual time. ``SystemClock.sleep``
        # delegates to ``asyncio.sleep`` whose ``CancelledError`` is
        # raised before any virtual-time effect lands; preserving
        # that ordering here keeps cancellation semantics aligned.
        await asyncio.sleep(0)
        self._sleep_calls.append(seconds)
        self._now = self._now + timedelta(seconds=seconds)

    def now(self) -> datetime:
        """Return the current virtual time."""
        return self._now

    def monotonic(self) -> float:
        """Return seconds since the per-instance epoch.

        Non-decreasing across ``sleep`` and ``advance`` calls because
        both only add non-negative deltas to ``_now``. The epoch is
        captured at ``__init__`` time and never changes.
        """
        return (self._now - self._epoch).total_seconds()

    def advance(self, seconds: float) -> None:
        """Advance virtual time without recording a sleep call.

        Args:
            seconds: Finite non-negative duration.

        Raises:
            ValueError: If ``seconds`` is negative or non-finite (NaN / inf).
        """
        _validate_seconds(seconds, action="advance")
        self._now = self._now + timedelta(seconds=seconds)

    async def advance_async(self, seconds: float) -> None:
        """Advance virtual time and yield to the event loop once.

        Useful in tests that need cooperative tasks (timers waiting on
        ``self.sleep``) to observe the new time before the next
        assertion. The single ``asyncio.sleep(0)`` lets every task
        currently awaiting on the event loop run one step.

        Args:
            seconds: Non-negative duration.
        """
        self.advance(seconds)
        await asyncio.sleep(0)

    @property
    def sleep_calls(self) -> tuple[float, ...]:
        """Seconds passed to every ``sleep`` call, in order."""
        return tuple(self._sleep_calls)
