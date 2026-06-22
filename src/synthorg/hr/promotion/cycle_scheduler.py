"""Periodic driver for the automatic promotion cycle.

Runs :meth:`PromotionService.run_cycle` on a fixed cadence so the org
re-evaluates agent seniority automatically rather than only when an
operator triggers a cycle by hand. Each tick scans active agents,
applies the changes the approval strategy auto-approves, and enqueues
human-gated changes as approval items (so the driver proposes but never
bypasses a human gate).

The delicate loop-bound lifecycle (primitives rebound to the running loop,
bounded stop-drain, per-tick kill-switch read) lives once in
:class:`~synthorg.core.scheduler.AsyncCycleScheduler`; this subclass supplies
only the promotion cadence work and the ``hr.promotion_cycle_paused``
kill-switch read.
"""

from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.hr.promotion.service import PromotionService
from synthorg.observability import get_logger
from synthorg.observability.events.promotion import (
    PROMOTION_CYCLE_RAN,
    PROMOTION_CYCLE_SCHEDULER_FAILED,
    PROMOTION_CYCLE_SCHEDULER_STARTED,
    PROMOTION_CYCLE_SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_PAUSE_NS: Final[str] = "hr"
_PAUSE_KEY: Final[str] = "promotion_cycle_paused"


class PromotionCycleScheduler(AsyncCycleScheduler):
    """Periodic background driver that runs the promotion cycle."""

    def __init__(
        self,
        service: PromotionService,
        *,
        interval_seconds: float,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The promotion service whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; must be >= 60 seconds.
            config_resolver: Optional resolver for the
                ``hr.promotion_cycle_paused`` kill-switch. When wired,
                every tick re-reads the flag so an operator can pause the
                cycle at runtime; without a resolver the loop runs
                unconditionally (matching the registered default of
                ``False`` / not-paused).

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="promotion-cycle-scheduler",
            started_event=PROMOTION_CYCLE_SCHEDULER_STARTED,
            stopped_event=PROMOTION_CYCLE_SCHEDULER_STOPPED,
            failed_event=PROMOTION_CYCLE_SCHEDULER_FAILED,
        )
        self._service = service
        self._config_resolver = config_resolver

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the cycle is enabled (inverts the paused flag).

        Reads ``hr.promotion_cycle_paused`` from the resolver and inverts
        it. Fail-safe to enabled (returns ``True``) when no resolver is
        wired or the read fails, so a settings-backend outage never
        silently halts promotions.

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
        """Run one promotion cycle, logging how many changes it applied."""
        applied = await self._service.run_cycle()
        logger.info(
            PROMOTION_CYCLE_RAN,
            applied=len(applied),
        )

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the promotion vocabulary."""
        logger.debug(
            PROMOTION_CYCLE_RAN,
            applied=0,
            note="paused_by_setting",
        )


__all__ = ["PromotionCycleScheduler"]
