# module-kind: code
"""Cadence for the sprint recovery sweep.

Boot is the pass that matters most, because a restart is when sprints are
stranded. The cadence exists for the other way a lifecycle hop is lost: the
spawned tail can die without the process going with it (a transient store
error the observer's best-effort handler swallows, a task group unwinding
around it), and nothing else would ever ask again.
"""

from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.engine.workflow.sprint_recovery import SprintRecoveryReconciler
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_TAIL_SCHEDULER_FAILED,
    SPRINT_TAIL_SCHEDULER_STARTED,
    SPRINT_TAIL_SCHEDULER_STOPPED,
    SPRINT_TAIL_SWEEP_PAUSED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Cadence when the operator has set none. Ten minutes: the boot pass covers
#: the restart case immediately, so this is the backstop for a tail lost
#: without its process, where reacting inside ten minutes is prompt and
#: sweeping a quiet deployment more often buys nothing.
DEFAULT_SPRINT_RESYNC_INTERVAL_SECONDS: Final[float] = 600.0

_TRIGGER: Final[str] = "periodic"

#: The label the boot pass carries. Declared beside the cadence's own label
#: and exported, so the one pass that runs outside this scheduler still
#: names itself from the same place rather than from a literal at the call
#: site that nothing holds to this vocabulary.
BOOT_TRIGGER: Final[str] = "boot"


class SprintTailScheduler(AsyncCycleScheduler):
    """Runs the sprint recovery sweep on a cadence.

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
        reconciler: SprintRecoveryReconciler,
        *,
        interval_seconds: float = DEFAULT_SPRINT_RESYNC_INTERVAL_SECONDS,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="sprint-tail-sweep",
            started_event=SPRINT_TAIL_SCHEDULER_STARTED,
            stopped_event=SPRINT_TAIL_SCHEDULER_STOPPED,
            failed_event=SPRINT_TAIL_SCHEDULER_FAILED,
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

        Fail-safe to running: a settings-backend outage must not silently
        strand every sprint, which is the exact failure the sweep recovers
        from. The sweep only advances lifecycle state and starts no agents,
        so running it is cheap and running it needlessly is harmless.

        Returns:
            ``True`` unless an operator has paused the sweep.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="engine",
            key="sprint_tail_sweep_paused",
            fallback=False,
        )
        return not paused

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the sprint vocabulary."""
        logger.debug(SPRINT_TAIL_SWEEP_PAUSED, trigger=_TRIGGER)

    @override
    async def _resolve_wait_interval(self) -> float:
        """Re-read the cadence so a change applies without a restart.

        Namespace and key spelled out rather than read from class vars: the
        liveness gate reads the call site textually, and a setting reached
        through an indirection it cannot follow reads as one nothing
        consumes.

        Returns:
            The resolved cadence, or the construction value when no
            resolver is wired.
        """
        if self._config_resolver is None:
            return self._interval
        return await self._config_resolver.get_float(
            "engine", "sprint_tail_resync_interval_seconds"
        )


__all__ = [
    "BOOT_TRIGGER",
    "DEFAULT_SPRINT_RESYNC_INTERVAL_SECONDS",
    "SprintTailScheduler",
]
