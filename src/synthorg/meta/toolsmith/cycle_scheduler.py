"""Autonomous detection driver for the self-extending toolkit.

Runs :meth:`ToolsmithService.run_cycle` on a fixed cadence so the org
detects a recurring capability gap automatically, rather than only when an
operator triggers a cycle by hand. Each tick polls the gap store for
recurring gaps and, for any found, authors + guards a new-tool proposal (the
guard chain still enqueues every proposal for human approval, so the periodic
driver proposes but never auto-applies).

The delicate loop-bound lifecycle (primitives rebound to the running loop,
bounded stop-drain, per-tick kill-switch read) lives once in
:class:`~synthorg.core.scheduler.AsyncCycleScheduler`; this subclass supplies
only the toolsmith cadence work and the ``meta.toolsmith_cycle_paused``
kill-switch read.
"""

from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.observability import get_logger
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_CYCLE_SCHEDULER_FAILED,
    TOOLSMITH_CYCLE_SCHEDULER_RAN,
    TOOLSMITH_CYCLE_SCHEDULER_STARTED,
    TOOLSMITH_CYCLE_SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_PAUSE_NS: Final[str] = "meta"
_PAUSE_KEY: Final[str] = "toolsmith_cycle_paused"


class ToolsmithCycleScheduler(AsyncCycleScheduler):
    """Periodic background driver that runs the toolsmith detection cycle."""

    def __init__(
        self,
        service: ToolsmithService,
        *,
        interval_seconds: float,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The toolsmith service whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; must be >= 60 seconds.
            config_resolver: Optional resolver for the
                ``meta.toolsmith_cycle_paused`` kill-switch. When wired,
                every tick re-reads the flag so an operator can pause the
                cycle at runtime; without a resolver the loop runs
                unconditionally (matching the registered default of
                ``False`` / not-paused).

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="toolsmith-cycle-scheduler",
            started_event=TOOLSMITH_CYCLE_SCHEDULER_STARTED,
            stopped_event=TOOLSMITH_CYCLE_SCHEDULER_STOPPED,
            failed_event=TOOLSMITH_CYCLE_SCHEDULER_FAILED,
        )
        self._service = service
        self._config_resolver = config_resolver

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the cycle is enabled (inverts the paused flag).

        Reads ``meta.toolsmith_cycle_paused`` from the resolver and inverts
        it. Fail-safe to enabled (returns ``True``) when no resolver is
        wired or the read fails, so a settings-backend outage never silently
        halts self-extension.

        Returns:
            ``True`` when the cycle should run this tick; ``False`` when an
            operator has paused it.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_PAUSE_NS,
            key=_PAUSE_KEY,
            fallback=False,
        )
        return not paused

    @override
    async def _run_cycle_once(self) -> None:
        """Run one detection cycle, logging how many proposals it produced.

        ``run_cycle`` catches per-gap authoring errors internally, so this
        only sees a systemic failure (which the base logs and survives).
        """
        proposals = await self._service.run_cycle()
        logger.info(
            TOOLSMITH_CYCLE_SCHEDULER_RAN,
            proposals=len(proposals),
        )

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the toolsmith vocabulary."""
        logger.debug(
            TOOLSMITH_CYCLE_SCHEDULER_RAN,
            proposals=0,
            note="paused_by_setting",
        )


__all__ = ["ToolsmithCycleScheduler"]
