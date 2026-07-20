"""Periodic driver for memory consolidation and retention.

Retention bounds and archival are per-agent properties with no natural
trigger on the request path, so they need a timer: without one, memory
grows to whatever the store allows and the
``memory.consolidation_enabled`` kill switch governs nothing.

The delicate loop-bound lifecycle (primitives rebound to the running
loop, bounded stop-drain, per-tick kill-switch read) lives once in
:class:`~synthorg.core.scheduler.AsyncCycleScheduler`; this subclass
supplies only the maintenance work and the kill-switch read.
"""

import builtins
from collections.abc import Awaitable, Callable
from typing import Final, override

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.service import MemoryConsolidationService
from synthorg.memory.enums import ConsolidationInterval
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    SCHEDULER_FAILED,
    SCHEDULER_RAN,
    SCHEDULER_STARTED,
    SCHEDULER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

type AgentIdSupplier = Callable[[], Awaitable[tuple[NotBlankStr, ...]]]
"""Returns the agents whose memory should be maintained this tick."""

_ENABLED_NS: Final[str] = "memory"
_ENABLED_KEY: Final[str] = "consolidation_enabled"

# One agent failing a tick is unremarkable (a transient store error, an
# agent deleted mid-sweep). The same agent failing every tick means its
# memory is never maintained again, which a stream of identical WARNINGs
# does not distinguish from the transient case, so a run of failures
# escalates once to ERROR.
_CONSECUTIVE_FAILURES_BEFORE_ESCALATION: Final[int] = 3

_SECONDS_PER_HOUR: Final[float] = 3600.0
_SECONDS_PER_DAY: Final[float] = 86400.0
_SECONDS_PER_WEEK: Final[float] = 604800.0

_INTERVAL_SECONDS: Final[dict[ConsolidationInterval, float]] = {
    ConsolidationInterval.HOURLY: _SECONDS_PER_HOUR,
    ConsolidationInterval.DAILY: _SECONDS_PER_DAY,
    ConsolidationInterval.WEEKLY: _SECONDS_PER_WEEK,
}


def interval_seconds_for(interval: ConsolidationInterval) -> float | None:
    """Map a configured interval to a scheduler cadence.

    Args:
        interval: The configured consolidation interval.

    Returns:
        The cadence in seconds, or ``None`` for ``never``. ``None`` means
        do not construct a scheduler at all rather than run one on a very
        long timer, so an operator choosing ``never`` sees no background
        task instead of a dormant one.
    """
    return _INTERVAL_SECONDS.get(interval)


class MemoryConsolidationScheduler(AsyncCycleScheduler):
    """Periodic background driver for consolidation and retention."""

    def __init__(
        self,
        service: MemoryConsolidationService,
        *,
        interval_seconds: float,
        agent_ids: AgentIdSupplier | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialise the scheduler.

        Args:
            service: The service whose ``run_maintenance`` is driven.
            interval_seconds: Cadence between maintenance runs.
            agent_ids: Supplier of the agents to maintain. Maintenance is
                per-agent (retention and max-memory bounds are per-agent
                properties), so without a supplier there is nothing to
                iterate and every tick is a no-op.
            config_resolver: Optional resolver for the
                ``memory.consolidation_enabled`` kill switch. When wired,
                every tick re-reads the flag so an operator can pause
                maintenance at runtime.

        Raises:
            ValueError: If ``interval_seconds`` is below the minimum.
        """
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="memory-consolidation-scheduler",
            started_event=SCHEDULER_STARTED,
            stopped_event=SCHEDULER_STOPPED,
            failed_event=SCHEDULER_FAILED,
        )
        self._service = service
        self._agent_ids = agent_ids
        self._config_resolver = config_resolver
        self._consecutive_failures: dict[NotBlankStr, int] = {}

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        """Return whether maintenance should run this tick.

        Fail-safe to enabled when no resolver is wired or the read
        fails: a settings-backend outage must not silently stop memory
        from being consolidated and pruned, because the failure would
        only surface much later as unbounded growth.

        Returns:
            ``True`` when maintenance should run this tick.
        """
        return await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=_ENABLED_NS,
            key=_ENABLED_KEY,
            fallback=True,
        )

    @override
    async def _run_cycle_once(self) -> None:
        """Run one maintenance pass over every agent.

        One agent's failure must not cost the rest their maintenance, so
        each is isolated; the base class surfaces a systemic failure.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        if self._agent_ids is None:
            logger.debug(SCHEDULER_RAN, agents=0, note="no_agent_supplier")
            return
        agent_ids = await self._agent_ids()
        # Drop failure state for agents no longer supplied, or a deleted
        # agent's streak lingers forever in a long-lived process with churn.
        supplied = set(agent_ids)
        self._consecutive_failures = {
            agent_id: streak
            for agent_id, streak in self._consecutive_failures.items()
            if agent_id in supplied
        }
        maintained = 0
        failed = 0
        for agent_id in agent_ids:
            try:
                await self._service.run_maintenance(agent_id)
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                failed += 1
                self._record_failure(agent_id, exc)
            else:
                maintained += 1
                self._consecutive_failures.pop(agent_id, None)
        logger.info(SCHEDULER_RAN, agents=maintained, failed=failed)

    def _record_failure(self, agent_id: NotBlankStr, exc: Exception) -> None:
        """Log one agent's maintenance failure, escalating a persistent run."""
        streak = self._consecutive_failures.get(agent_id, 0) + 1
        self._consecutive_failures[agent_id] = streak
        log = (
            logger.error
            if streak >= _CONSECUTIVE_FAILURES_BEFORE_ESCALATION
            else logger.warning
        )
        log(
            SCHEDULER_FAILED,
            agent_id=agent_id,
            consecutive_failures=streak,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    @override
    def _log_cycle_paused(self) -> None:
        """Log a paused tick under the consolidation vocabulary."""
        logger.debug(SCHEDULER_RAN, note="paused_by_setting")


__all__ = [
    "AgentIdSupplier",
    "MemoryConsolidationScheduler",
    "interval_seconds_for",
]
