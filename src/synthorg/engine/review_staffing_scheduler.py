# module-kind: code
"""Cadence for the review-staffing sweep.

The sweep is the guarantee, not an optimisation: a role can be filled by a
dashboard edit, an approved hire, or a config load at boot, and none of those
announce themselves to the gate that parked the work. Something has to keep
asking, so this does, on an operator-tunable cadence re-read per tick.
"""

from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.engine.review_staffing_reconciler import ReviewStaffingReconciler
from synthorg.observability.events.review_staffing import (
    REVIEW_STAFFING_SCHEDULER_FAILED,
    REVIEW_STAFFING_SCHEDULER_STARTED,
    REVIEW_STAFFING_SCHEDULER_STOPPED,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

#: Cadence when the operator has set none. Fifteen minutes: a parked task is
#: waiting on a human staffing decision, so reacting within a quarter of an
#: hour is prompt without sweeping a quiet backlog every minute.
DEFAULT_RESYNC_INTERVAL_SECONDS: Final[float] = 900.0

_TRIGGER: Final[str] = "periodic"


class ReviewStaffingScheduler(AsyncCycleScheduler):
    """Runs the staffing sweep on a cadence.

    Args:
        reconciler: The sweep to run.
        interval_seconds: Starting cadence; re-resolved per tick so an
            operator change applies without a restart.
        config_resolver: Reads the live cadence. ``None`` keeps the
            construction-time value for the process's life.
    """

    def __init__(
        self,
        reconciler: ReviewStaffingReconciler,
        *,
        interval_seconds: float = DEFAULT_RESYNC_INTERVAL_SECONDS,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="review-staffing-sweep",
            started_event=REVIEW_STAFFING_SCHEDULER_STARTED,
            stopped_event=REVIEW_STAFFING_SCHEDULER_STOPPED,
            failed_event=REVIEW_STAFFING_SCHEDULER_FAILED,
        )
        self._reconciler = reconciler
        self._config_resolver = config_resolver

    @override
    async def _run_cycle_once(self) -> None:
        """Run one sweep."""
        await self._reconciler.reconcile(trigger=_TRIGGER)

    @override
    async def _resolve_wait_interval(self) -> float:
        """Re-read the cadence so a change applies without a restart.

        Namespace and key spelled out rather than read from class vars: the
        liveness gate reads the call site textually, and an indirection it
        cannot follow reads as a setting nothing consumes.

        Returns:
            The resolved cadence, or the construction value when no resolver
            is wired.
        """
        if self._config_resolver is None:
            return self._interval
        return await self._config_resolver.get_float(
            "engine", "review_staffing_resync_interval_seconds"
        )


__all__ = ["DEFAULT_RESYNC_INTERVAL_SECONDS", "ReviewStaffingScheduler"]
