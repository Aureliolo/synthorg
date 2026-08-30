"""Shared pin-check test double and event-loop settle helper.

Used by both sandbox lifecycle strategies' test suites
(``PerAgentStrategy``'s grace/idle timers, ``PerTaskStrategy``'s
pinned-release teardown): each drives a background task through a
``FakeClock``-timed wait-then-destroy loop, and both need the same two
things -- a pin predicate whose call count a test can assert on, and a
fixed number of event-loop yields for that task to acquire the
strategy's lock, mutate its bookkeeping, and (on the final iteration)
call ``destroy_fn``.
"""

import asyncio

#: Number of event-loop yields needed for a timer/deferred-teardown
#: task to finish one iteration: it awaits the (no-op) clock sleep,
#: takes the strategy lock, mutates the bookkeeping dicts, then awaits
#: the user-supplied destroy_fn. Five yields covers the worst-case
#: async-step count without padding.
SETTLE_TICKS: int = 5

__all__ = ["SETTLE_TICKS", "CountingPin", "settle"]


async def settle(ticks: int = SETTLE_TICKS) -> None:
    """Yield control ``ticks`` times so a scheduled task can complete."""
    for _ in range(ticks):
        await asyncio.sleep(0)


class CountingPin:
    """A pin_check stub reporting live for the first *live_for* calls.

    Mirrors the shape ``BackgroundJobRegistry.has_live_jobs`` will use:
    an async predicate keyed by container_id. Recording every call lets
    a test assert the recheck loop actually ran rather than merely
    inferring it from the eventual outcome.
    """

    def __init__(self, live_for: int) -> None:
        self._remaining = live_for
        self.calls: list[str] = []

    async def __call__(self, container_id: str) -> bool:
        self.calls.append(container_id)
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True
