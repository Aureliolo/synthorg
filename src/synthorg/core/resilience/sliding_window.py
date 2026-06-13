"""Per-key sliding-window event limiter.

A small in-process limiter that admits at most ``max_events`` per key
within a rolling time window. It lives in ``core`` because several
layers need the same admission model without depending on each other:
the engine classification sink uses it to throttle notification fanout,
and the API SSE / WebSocket revalidation loops use it to budget
consecutive auth-revalidation failures per connection.

The limiter is intentionally simple and in-process: state is per
instance and is not shared across processes or tasks beyond the owning
event loop.
"""

import asyncio
import time
from collections.abc import Callable


class SlidingWindowEventLimiter:
    """Per-key sliding-window event limiter.

    Tracks the timestamps of recent ``take`` calls per key and admits a
    new call only when the number of timestamps inside the configured
    window is strictly less than ``max_events``. Older timestamps are
    pruned on every call.

    Args:
        max_events: Maximum admissions per sliding window. Must be >= 1.
        window_seconds: Length of the sliding window in seconds. Must
            be > 0.
        clock: Optional monotonic clock, injectable for tests.

    Raises:
        ValueError: If ``max_events < 1`` or ``window_seconds <= 0``.
    """

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_events < 1:
            msg = "max_events must be >= 1"
            raise ValueError(msg)
        if window_seconds <= 0:
            msg = "window_seconds must be > 0"
            raise ValueError(msg)
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._clock: Callable[[], float] = clock or time.monotonic
        # Each per-key entry stores ``(handle, timestamp)`` pairs. The
        # ``handle`` is an opaque ``object`` minted by ``take`` and
        # returned to the caller; ``release`` removes that exact entry
        # rather than popping the latest timestamp -- otherwise a slow
        # admission whose dispatch fails could refund a *later*
        # admission's slot, leaving the failed admission counted.
        self._events: dict[str, list[tuple[object, float]]] = {}
        self._lock = asyncio.Lock()

    async def take(self, key: str) -> object | None:
        """Attempt to consume one admission for ``key``.

        Returns an opaque admission handle when the slot was granted
        (the caller may proceed and must pass the same handle to
        ``release`` if the downstream action ultimately fails) or
        ``None`` when the window is saturated. The handle is an
        ``object()`` instance with no public attributes; callers MUST
        treat it as opaque and only use it for ``release``.

        Prunes stale entries for idle keys on each call to prevent
        unbounded growth of ``_events`` from one-off keys.

        Returns:
            An opaque admission handle on success; ``None`` when the
            sliding window is saturated for ``key``.

        The dict reads / writes execute under ``self._lock`` so two
        concurrent ``take()`` calls cannot both observe ``len(events)
        < max_events`` and admit beyond the configured budget.
        """
        async with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            # Prune idle keys whose latest timestamp is outside the window.
            stale_keys = [
                k
                for k, entries in self._events.items()
                if k != key and (not entries or entries[-1][1] <= cutoff)
            ]
            for k in stale_keys:
                del self._events[k]
            entries = [
                (handle, ts) for handle, ts in self._events.get(key, []) if ts > cutoff
            ]
            if len(entries) >= self._max_events:
                self._events[key] = entries
                return None
            handle: object = object()
            entries.append((handle, now))
            self._events[key] = entries
            return handle

    async def release(self, key: str, handle: object) -> None:
        """Refund the exact admission identified by ``handle``.

        Call this when a ``take`` succeeded but the downstream action
        failed, so the slot can be reused by the next attempt. Only
        the entry whose handle is identical (``is``) to the supplied
        one is removed; if no match is found the call is a silent
        no-op (the handle expired out of the window before the failure
        was detected, which is a benign race).
        """
        async with self._lock:
            entries = self._events.get(key)
            if not entries:
                return
            remaining = [(h, ts) for h, ts in entries if h is not handle]
            if remaining:
                self._events[key] = remaining
            else:
                del self._events[key]


def build_revalidation_limiter(
    *,
    max_failures: int,
    window_seconds: float,
    interval_seconds: float,
) -> SlidingWindowEventLimiter:
    """Build a revalidation failure limiter with a tick-aware window.

    A revalidation loop performs one check per ``interval_seconds``. A
    sliding window measured in wall-clock seconds shorter than
    ``max_failures`` ticks can never accumulate enough failures to
    saturate (the oldest failure ages out before the next tick), which
    would hold a stale-auth stream open indefinitely (fail-open). Clamp
    the effective window so ``max_failures`` consecutive failed ticks
    fall inside it while isolated blips still age out.

    Args:
        max_failures: Admissions allowed before the window saturates.
        window_seconds: Operator-configured nominal window.
        interval_seconds: Loop tick interval (one check per interval).

    Returns:
        A :class:`SlidingWindowEventLimiter` whose window is the larger
        of ``window_seconds`` and ``interval_seconds * max_failures``.
    """
    effective_window = max(
        float(window_seconds),
        float(interval_seconds) * max_failures,
    )
    return SlidingWindowEventLimiter(
        max_events=max_failures,
        window_seconds=effective_window,
    )
