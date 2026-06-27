"""Periodic driver for the closed-loop evaluation cycle.

Runs :meth:`EvalLoopCoordinator.run_cycle` on a fixed cadence so the org
re-evaluates every agent's five-pillar performance automatically rather than
only when an operator triggers a cycle by hand. Opt-in (off by default): a
cycle can route corrective actions to the training pipeline, so the background
driver only starts when ``hr.eval_loop_cycle_enabled`` is set.

The delicate loop-bound lifecycle (primitives rebound to the running loop,
bounded stop-drain, per-tick kill-switch read) lives once in
:class:`~synthorg.core.scheduler.AsyncCycleScheduler`; this subclass supplies
only the evaluation cadence work and the ``hr.eval_loop_cycle_paused``
kill-switch read.
"""

from datetime import timedelta
from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.observability import get_logger
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_CYCLE_RAN,
    EVAL_LOOP_CYCLE_SCHEDULER_FAILED,
    EVAL_LOOP_CYCLE_SCHEDULER_STARTED,
    EVAL_LOOP_CYCLE_SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_PAUSE_NS: Final[str] = "hr"
_PAUSE_KEY: Final[str] = "eval_loop_cycle_paused"


class EvalLoopCycleScheduler(AsyncCycleScheduler):
    """Periodic background driver that runs the evaluation loop cycle."""

    def __init__(
        self,
        coordinator: EvalLoopCoordinator,
        *,
        interval_seconds: float,
        window: timedelta,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            coordinator: The coordinator whose ``run_cycle`` is driven.
            interval_seconds: Cadence between cycles; must be >= 60 seconds.
            window: Look-back window each cycle collects metrics over.
            config_resolver: Optional resolver for the
                ``hr.eval_loop_cycle_paused`` kill-switch. When wired, every
                tick re-reads the flag so an operator can pause the cycle at
                runtime; without a resolver the loop runs unconditionally
                (matching the registered default of ``False`` / not-paused).

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="eval-loop-cycle-scheduler",
            started_event=EVAL_LOOP_CYCLE_SCHEDULER_STARTED,
            stopped_event=EVAL_LOOP_CYCLE_SCHEDULER_STOPPED,
            failed_event=EVAL_LOOP_CYCLE_SCHEDULER_FAILED,
        )
        self._coordinator = coordinator
        self._window = window
        self._config_resolver = config_resolver

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether the cycle is enabled (inverts the paused flag).

        Reads ``hr.eval_loop_cycle_paused`` from the resolver and inverts it.
        Fail-safe to enabled (returns ``True``) when no resolver is wired or
        the read fails, so a settings-backend outage never silently halts the
        loop the operator opted into.

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
        """Run one evaluation cycle, logging how many agents it evaluated."""
        report = await self._coordinator.run_cycle(window=self._window)
        logger.info(
            EVAL_LOOP_CYCLE_RAN,
            cycle_id=str(report.cycle_id),
            agents_evaluated=report.agents_evaluated,
            training_triggered=report.training_triggered,
        )

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the evaluation-loop vocabulary."""
        logger.debug(
            EVAL_LOOP_CYCLE_RAN,
            agents_evaluated=0,
            note="paused_by_setting",
        )


__all__ = ["EvalLoopCycleScheduler"]
