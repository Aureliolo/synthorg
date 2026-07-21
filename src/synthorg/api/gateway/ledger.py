# module-kind: code
"""In-process per-run cost ledger for the gateway hard token kill.

Tracks accumulated LLM cost per ``execution_id`` so the gateway can
refuse a further call once a run has spent its ceiling. This mirrors the
engine's boundary-checked ``run_hard_ceiling`` semantics: the check is
pre-flight per call (an in-flight call is allowed to finish), so total
spend is bounded by ``ceiling`` plus at most one final call. The ledger
is per-process; a run that outlives a restart re-mints and starts fresh.

Entries are bounded two ways so the map cannot grow unboundedly over a
long-lived process: the gateway resets a run on its terminal budget kill,
and any entry idle past ``entry_ttl_seconds`` (a run makes no call for
longer than its bearer could live) is evicted lazily on the next ``add``.
"""

import asyncio
from typing import Final

from synthorg.core.clock import Clock, SystemClock

# A run that has made no gateway call for longer than this is treated as
# dead and evicted; comfortably exceeds a bearer's lifetime, so an active
# run (which calls per turn) is never evicted mid-flight.
_DEFAULT_ENTRY_TTL_SECONDS: Final[float] = 3600.0


class RunCostLedger:
    """Coroutine-safe accumulator of per-run LLM cost with idle eviction."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        entry_ttl_seconds: float = _DEFAULT_ENTRY_TTL_SECONDS,
    ) -> None:
        self._totals: dict[str, float] = {}
        self._touched: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._clock = clock if clock is not None else SystemClock()
        self._entry_ttl_seconds = entry_ttl_seconds

    async def add(self, execution_id: str, cost: float) -> float:
        """Add *cost* to the run's total and return the new total.

        Evicts entries idle past ``entry_ttl_seconds`` first, so a run that
        completes without a terminal kill cannot linger for the process's
        lifetime.

        Args:
            execution_id: The run to attribute the cost to.
            cost: Non-negative cost to add.

        Returns:
            The run's new accumulated total.
        """
        async with self._lock:
            now = self._clock.monotonic()
            self._evict_idle(now)
            total = self._totals.get(execution_id, 0.0) + max(0.0, cost)
            self._totals[execution_id] = total
            self._touched[execution_id] = now
            return total

    async def total(self, execution_id: str) -> float:
        """Return the run's accumulated total, or ``0.0`` if unseen.

        Args:
            execution_id: The run to look up.

        Returns:
            The accumulated cost for the run.
        """
        async with self._lock:
            return self._totals.get(execution_id, 0.0)

    async def reset(self, execution_id: str) -> None:
        """Forget the run's accumulated total.

        Called on a run's terminal budget kill; also reachable for teardown.

        Args:
            execution_id: The run to clear.
        """
        async with self._lock:
            self._totals.pop(execution_id, None)
            self._touched.pop(execution_id, None)

    def _evict_idle(self, now: float) -> None:
        """Drop entries not touched within ``entry_ttl_seconds`` (lock held)."""
        stale = [
            execution_id
            for execution_id, touched in self._touched.items()
            if now - touched > self._entry_ttl_seconds
        ]
        for execution_id in stale:
            self._totals.pop(execution_id, None)
            self._touched.pop(execution_id, None)
