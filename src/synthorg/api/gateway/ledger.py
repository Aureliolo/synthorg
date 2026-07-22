# module-kind: code
"""In-process per-run cost ledger for the gateway hard token kill.

Tracks accumulated LLM cost per ``execution_id`` so the gateway can
refuse a further call once a run has spent its ceiling. This mirrors the
engine's boundary-checked ``run_hard_ceiling`` semantics: the check is
pre-flight per call (an in-flight call is allowed to finish), so total
spend is bounded by ``ceiling`` plus at most one final call. The ledger
is per-process; a run that outlives a restart re-mints and starts fresh.

A run that crosses its ceiling is *latched* killed, not forgotten: its
total stays pinned so reusing the still-valid bearer cannot respend the
ceiling (a reset would zero the total and re-admit the next call). The map
is bounded by lazy idle eviction: any entry (killed or live) idle past
``entry_ttl_seconds`` (a run makes no call for longer than its bearer could
live) is dropped on the next ``add``, so killed runs are reclaimed once
their bearer can no longer be used, not before.
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
        self._killed: set[str] = set()
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

    async def kill(self, execution_id: str, spent: float) -> None:
        """Latch a run as budget-exhausted so every later call stays rejected.

        Unlike :meth:`reset`, the run is not forgotten: its total is pinned at
        (at least) *spent* and the run is marked killed, so reusing the
        still-valid bearer cannot zero the ledger and respend the ceiling. The
        entry is released only by idle eviction (past a bearer's lifetime) or a
        process restart.

        Args:
            execution_id: The run that crossed its ceiling.
            spent: The accumulated cost at the crossing (pinned as the floor).
        """
        async with self._lock:
            now = self._clock.monotonic()
            self._evict_idle(now)
            self._killed.add(execution_id)
            self._totals[execution_id] = max(
                self._totals.get(execution_id, 0.0), 0.0, spent
            )
            self._touched[execution_id] = now

    async def is_killed(self, execution_id: str) -> bool:
        """Return whether a run has been budget-killed, refreshing its liveness.

        Touches the entry when killed so an actively-retrying run keeps its
        latch: idle eviction then only reclaims a killed run that has genuinely
        gone quiet past a bearer's lifetime, never one still trying to spend.

        Returns:
            ``True`` when the run is latched killed.
        """
        async with self._lock:
            if execution_id in self._killed:
                self._touched[execution_id] = self._clock.monotonic()
                return True
            return False

    async def reset(self, execution_id: str) -> None:
        """Forget the run's accumulated total (teardown only, never a kill).

        A budget kill must latch via :meth:`kill`, not reset, or the next call
        on the same bearer would start from zero and respend the ceiling.

        Args:
            execution_id: The run to clear.
        """
        async with self._lock:
            self._totals.pop(execution_id, None)
            self._touched.pop(execution_id, None)
            self._killed.discard(execution_id)

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
            self._killed.discard(execution_id)
