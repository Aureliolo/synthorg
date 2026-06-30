"""Periodic driver for the closed-loop evaluation cycle.

Runs :meth:`EvalLoopCoordinator.run_cycle` on a cadence so the org re-evaluates
every agent's five-pillar performance automatically rather than only when an
operator triggers a cycle by hand. Ghost-wired and opt-in (off by default): the
scheduler is always constructed and started, but every tick short-circuits
until ``hr.eval_loop_cycle_enabled`` is set (a cycle can route corrective
actions to the training pipeline). The master switch, the
``hr.eval_loop_cycle_paused`` flag, the cadence, and the look-back window are
all re-read live per tick, so an operator can enable, pause, retune, or disable
the cycle with no restart.

The delicate loop-bound lifecycle (primitives rebound to the running loop,
bounded stop-drain, per-tick enabled + interval reads) lives once in
:class:`~synthorg.core.scheduler.AsyncCycleScheduler`; this subclass supplies
the evaluation cadence work, the two-flag enabled check
(``hr.eval_loop_cycle_enabled`` and ``hr.eval_loop_cycle_paused``), and the
per-tick cadence / window re-reads.
"""

import math
from datetime import timedelta
from typing import Final, override

from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.hr.evaluation.loop_coordinator import EvalLoopCoordinator
from synthorg.observability import get_logger
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_CYCLE_PAUSED,
    EVAL_LOOP_CYCLE_RAN,
    EVAL_LOOP_CYCLE_SCHEDULER_FAILED,
    EVAL_LOOP_CYCLE_SCHEDULER_STARTED,
    EVAL_LOOP_CYCLE_SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import (
    resolve_bool_with_fallback,
    resolve_float_with_fallback,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_HR_NS: Final[str] = "hr"
_ENABLED_KEY: Final[str] = "eval_loop_cycle_enabled"
_PAUSE_KEY: Final[str] = "eval_loop_cycle_paused"
_INTERVAL_KEY: Final[str] = "eval_loop_cycle_interval_seconds"
_WINDOW_KEY: Final[str] = "eval_loop_cycle_window_hours"
_SECONDS_PER_HOUR: Final[float] = 3600.0
# Upper bound on a live-resolved window: ``timedelta(seconds=...)`` raises
# ``OverflowError`` past ``timedelta.max``, so a finite-but-enormous setting must
# fall back rather than reach the constructor.
_MAX_WINDOW_SECONDS: Final[float] = timedelta.max.total_seconds()


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
            config_resolver: Optional resolver for the per-tick master-switch
                (``hr.eval_loop_cycle_enabled``), pause flag
                (``hr.eval_loop_cycle_paused``), and cadence
                (``hr.eval_loop_cycle_interval_seconds``) reads. Without a
                resolver the master switch fail-safes to ``False`` (disabled),
                so the loop never runs until a resolver is wired; this ensures a
                resolver outage cannot silently start a cycle that routes
                corrective actions to training.

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
        """Return whether the cycle should run this tick.

        Folds the master switch and the pause kill-switch into one per-tick
        read so both apply with no restart: the scheduler is always constructed
        and started, but only does work when ``hr.eval_loop_cycle_enabled`` is
        set AND ``hr.eval_loop_cycle_paused`` is not.

        The master switch is opt-in (default off), so its fail-safe is
        ``False`` (disabled): a settings-backend outage must not silently start
        a loop that routes corrective actions to training. The pause flag
        fail-safes to ``False`` (not paused), so an outage does not silently
        halt a loop the operator opted into.

        Returns:
            ``True`` when the cycle should run this tick; ``False`` when the
            master switch is off or an operator has paused it.
        """
        enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_HR_NS,
            key=_ENABLED_KEY,
            fallback=False,
        )
        if not enabled:
            return False
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_HR_NS,
            key=_PAUSE_KEY,
            fallback=False,
        )
        return not paused

    @override
    async def _resolve_wait_interval(self) -> float:
        """Re-read the cadence per tick so a change applies with no restart.

        Fail-safe to the construction-time interval on a resolver outage.

        Returns:
            The live ``hr.eval_loop_cycle_interval_seconds`` value.
        """
        return await resolve_float_with_fallback(
            resolver=self._config_resolver,
            namespace=_HR_NS,
            key=_INTERVAL_KEY,
            fallback=self._interval,
        )

    @override
    async def _run_cycle_once(self) -> None:
        """Run one evaluation cycle over the live look-back window.

        The window is re-read each tick (fail-safe to the construction-time
        window) so an operator can widen / narrow it with no restart.
        """
        fallback_hours = self._window.total_seconds() / _SECONDS_PER_HOUR
        window_hours = await resolve_float_with_fallback(
            resolver=self._config_resolver,
            namespace=_HR_NS,
            key=_WINDOW_KEY,
            fallback=fallback_hours,
        )
        # The resolver only fails over on a read error; a stored nan, inf,
        # non-positive, or finite-but-enormous value still reaches here and
        # would build a nonsensical timedelta -- or raise ``OverflowError`` past
        # ``timedelta.max``. Collapse any of those to the last-known-good window
        # so the runtime path matches the resolver-outage fallback.
        if (
            not math.isfinite(window_hours)
            or window_hours <= 0
            or window_hours * _SECONDS_PER_HOUR >= _MAX_WINDOW_SECONDS
        ):
            logger.warning(
                EVAL_LOOP_CYCLE_SCHEDULER_FAILED,
                note="window_read_invalid",
                window_hours=window_hours,
            )
            window_hours = fallback_hours
        window = timedelta(seconds=window_hours * _SECONDS_PER_HOUR)
        report = await self._coordinator.run_cycle(window=window)
        logger.info(
            EVAL_LOOP_CYCLE_RAN,
            cycle_id=str(report.cycle_id),
            agents_evaluated=report.agents_evaluated,
            training_triggered=report.training_triggered,
        )

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the evaluation-loop vocabulary."""
        logger.debug(EVAL_LOOP_CYCLE_PAUSED)


__all__ = ["EvalLoopCycleScheduler"]
