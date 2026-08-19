# module-kind: code
"""Cadence for the run-recovery sweep.

Boot is the pass that matters most, because a restart is when runs are
stranded. The cadence exists for the other way a driver dies: a dispatch task
can be lost without the process going with it (an unhandled cancellation, a
task group unwinding around it), and nothing else would ever ask again.
"""

from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.engine.run_recovery.reconciler import RunRecoveryReconciler
from synthorg.observability import get_logger
from synthorg.observability.events.run_recovery import (
    RUN_RECOVERY_SCHEDULER_FAILED,
    RUN_RECOVERY_SCHEDULER_STARTED,
    RUN_RECOVERY_SCHEDULER_STOPPED,
    RUN_RECOVERY_SWEEP_PAUSED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Cadence when the operator has set none. Ten minutes: the boot pass covers
#: the restart case immediately, so this is the backstop for a driver lost
#: without its process, where reacting inside ten minutes is prompt and
#: sweeping a quiet deployment more often buys nothing.
DEFAULT_RESYNC_INTERVAL_SECONDS: Final[float] = 600.0

_TRIGGER: Final[str] = "periodic"


class RunRecoveryScheduler(AsyncCycleScheduler):
    """Runs the recovery sweep on a cadence.

    Args:
        reconciler: The sweep to run.
        interval_seconds: Starting cadence; re-resolved per tick so an
            operator change applies without a restart.
        config_resolver: Reads the live cadence and the pause switch.
            ``None`` keeps the construction-time cadence for the process's
            life and leaves the sweep unpausable.
    """

    def __init__(
        self,
        reconciler: RunRecoveryReconciler,
        *,
        interval_seconds: float = DEFAULT_RESYNC_INTERVAL_SECONDS,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="run-recovery-sweep",
            started_event=RUN_RECOVERY_SCHEDULER_STARTED,
            stopped_event=RUN_RECOVERY_SCHEDULER_STOPPED,
            failed_event=RUN_RECOVERY_SCHEDULER_FAILED,
        )
        self._reconciler = reconciler
        self._config_resolver = config_resolver

    @override
    async def _run_cycle_once(self) -> None:
        """Run one sweep."""
        await self._reconciler.reconcile(trigger=_TRIGGER)

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the sweep runs this tick.

        The sweep transitions tasks and starts dispatches, which spends, so
        an operator needs a way to stop it without stopping the process.
        Fail-safe to running: a settings-backend outage must not silently
        strand every run, which is the exact failure the sweep recovers from.

        Returns:
            ``True`` unless an operator has paused the sweep.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="engine",
            key="run_recovery_sweep_paused",
            fallback=False,
        )
        return not paused

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the run-recovery vocabulary."""
        logger.debug(RUN_RECOVERY_SWEEP_PAUSED, trigger=_TRIGGER)

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
            "engine", "run_recovery_resync_interval_seconds"
        )


__all__ = ["DEFAULT_RESYNC_INTERVAL_SECONDS", "RunRecoveryScheduler"]
