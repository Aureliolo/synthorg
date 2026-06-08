"""Inflection-based evolution trigger.

Subscribes to performance inflection events via the
``InflectionSink`` protocol. When a metric's trend direction
changes, the trigger fires for the affected agent.
"""

import asyncio

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.protocols import EvolutionContext
from synthorg.hr.performance.inflection_protocol import (
    PerformanceInflection,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_TRIGGER_REQUESTED,
    EVOLUTION_TRIGGER_SKIPPED,
)

logger = get_logger(__name__)


class InflectionTrigger:
    """Trigger that fires on performance inflection events.

    Implements both ``EvolutionTrigger`` and ``InflectionSink``
    protocols. The performance tracker emits inflection events to
    this sink, which queues them. ``should_trigger`` checks if
    there are pending inflections for the agent.
    """

    _MAX_PENDING_PER_AGENT: int = 1000

    def __init__(self) -> None:
        self._pending: dict[str, list[PerformanceInflection]] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Trigger name."""
        return "inflection"

    async def emit(
        self,
        inflection: PerformanceInflection,
    ) -> None:
        """Receive a performance inflection event (InflectionSink)."""
        key = str(inflection.agent_id)
        async with self._lock:
            if key not in self._pending:
                self._pending[key] = []
            # Cap queue: evict oldest to keep most recent inflections
            if len(self._pending[key]) >= self._MAX_PENDING_PER_AGENT:
                self._pending[key].pop(0)
                logger.info(
                    EVOLUTION_TRIGGER_SKIPPED,
                    agent_id=key,
                    trigger="inflection",
                    reason="queue_eviction",
                )
            self._pending[key].append(inflection)

    async def should_trigger(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,  # noqa: ARG002
    ) -> bool:
        """Trigger if there are pending inflections for the agent.

        Returns:
            ``True`` when at least one inflection is pending (and
            consumed by this call); ``False`` otherwise.
        """
        key = str(agent_id)
        async with self._lock:
            pending = self._pending.pop(key, [])
        if pending:
            logger.info(
                EVOLUTION_TRIGGER_REQUESTED,
                agent_id=key,
                trigger="inflection",
                inflection_count=len(pending),
            )
            return True
        return False

    async def get_pending(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[PerformanceInflection, ...]:
        """Peek at pending inflections without consuming them.

        Returns:
            Tuple of pending inflections for ``agent_id``; ``()`` when
            none are queued.
        """
        async with self._lock:
            return tuple(self._pending.get(str(agent_id), []))
