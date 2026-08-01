# module-kind: code
"""Periodic reconcile sweep.

Every other trigger is an optimisation. This one is the guarantee: a
dependency can arrive with nothing to announce it (a local model server
coming back up, a network partition healing), and no event will ever fire.
A level-triggered design tolerates that only because something keeps
asking.
"""

from typing import override

from synthorg.api.state import AppState
from synthorg.api.subsystems.runtime import reconcile_subsystems
from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.observability.events.subsystem import (
    SUBSYSTEM_ACTIVATION_FAILED,
    SUBSYSTEM_RESYNC_STARTED,
    SUBSYSTEM_RESYNC_STOPPED,
)
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

_NAMESPACE = "api"
_INTERVAL_KEY = "subsystem_resync_interval_seconds"


class SubsystemResyncScheduler(AsyncCycleScheduler):
    """Runs a reconcile pass on a cadence.

    Args:
        app_state: Application state the pass reads and wires.
        interval_seconds: Starting cadence; re-resolved per cycle so an
            operator change applies without a restart.
    """

    def __init__(self, app_state: AppState, *, interval_seconds: float) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="subsystem-resync",
            started_event=SUBSYSTEM_RESYNC_STARTED,
            stopped_event=SUBSYSTEM_RESYNC_STOPPED,
            failed_event=SUBSYSTEM_ACTIVATION_FAILED,
        )
        self._app_state = app_state

    @override
    async def _run_cycle_once(self) -> None:
        """Run one reconcile pass."""
        await reconcile_subsystems(self._app_state, trigger="resync")

    @override
    async def _resolve_wait_interval(self) -> float:
        """Re-read the cadence so a change applies without a restart.

        Returns:
            The resolved interval, or the boot value when no resolver is
            wired yet.
        """
        if self._app_state.slice(SettingsStateSlice).config_resolver is None:
            return self._interval
        return await config_resolver_of(self._app_state).get_float(
            _NAMESPACE,
            _INTERVAL_KEY,
        )
