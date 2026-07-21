# module-kind: code
"""In-process per-run cost ledger for the gateway hard token kill.

Tracks accumulated LLM cost per ``execution_id`` so the gateway can
refuse a further call once a run has spent its ceiling. This mirrors the
engine's boundary-checked ``run_hard_ceiling`` semantics: the check is
pre-flight per call (an in-flight call is allowed to finish), so total
spend is bounded by ``ceiling`` plus at most one final call. The ledger
is per-process; a run that outlives a restart re-mints and starts fresh.
"""

import asyncio


class RunCostLedger:
    """Thread-safe accumulator of per-run LLM cost."""

    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def add(self, execution_id: str, cost: float) -> float:
        """Add *cost* to the run's total and return the new total.

        Args:
            execution_id: The run to attribute the cost to.
            cost: Non-negative cost to add.

        Returns:
            The run's new accumulated total.
        """
        async with self._lock:
            total = self._totals.get(execution_id, 0.0) + max(0.0, cost)
            self._totals[execution_id] = total
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

        Args:
            execution_id: The run to clear (e.g. on teardown).
        """
        async with self._lock:
            self._totals.pop(execution_id, None)
