"""Batched (time-interval) evolution trigger.

Fires when enough time has passed since the last evolution run
for an agent. Default interval is one day (86400 seconds).
"""

import asyncio
from datetime import UTC, datetime
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.protocols import EvolutionContext
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_TRIGGER_REQUESTED,
    EVOLUTION_TRIGGER_RUN_RECORDED,
    EVOLUTION_TRIGGER_SKIPPED,
)

logger = get_logger(__name__)

_SECONDS_PER_DAY: Final[int] = 86_400


class BatchedTrigger:
    """Trigger that fires on a time interval.

    Tracks the last evolution time per agent and triggers when
    the configured interval has elapsed.

    Args:
        interval_seconds: Minimum seconds between evolutions.
    """

    def __init__(
        self,
        *,
        interval_seconds: int = _SECONDS_PER_DAY,
    ) -> None:
        self._interval = max(1, interval_seconds)
        self._last_run: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Trigger name."""
        return "batched"

    async def should_trigger(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,  # noqa: ARG002
    ) -> bool:
        """Trigger if the interval has elapsed since last run.

        Returns:
            ``True`` when the configured interval has elapsed since
            the last fire for ``agent_id`` (or no previous fire is
            recorded); ``False`` otherwise.
        """
        key = str(agent_id)
        now = datetime.now(UTC)
        async with self._lock:
            last = self._last_run.get(key)
            elapsed = (now - last).total_seconds() if last is not None else None
            fire = elapsed is None or elapsed >= self._interval
            if fire:
                self._last_run[key] = now

        if not fire:
            logger.debug(
                EVOLUTION_TRIGGER_SKIPPED,
                agent_id=key,
                trigger="batched",
                reason="interval_not_elapsed",
                elapsed_seconds=int(elapsed) if elapsed is not None else None,
                interval_seconds=self._interval,
            )
            return False

        logger.info(
            EVOLUTION_TRIGGER_REQUESTED,
            agent_id=key,
            trigger="batched",
            recorded_at=now.isoformat(),
            previous_run_at=last.isoformat() if last is not None else None,
        )
        return True

    async def record_run(self, agent_id: NotBlankStr) -> None:
        """Record that an evolution run completed for an agent.

        Acquires the internal lock so writes cannot race against a
        concurrent :meth:`should_trigger` read/write on the same key.
        """
        key = str(agent_id)
        async with self._lock:
            previous = self._last_run.get(key)
            now = datetime.now(UTC)
            self._last_run[key] = now
        logger.info(
            EVOLUTION_TRIGGER_RUN_RECORDED,
            agent_id=key,
            trigger="batched",
            recorded_at=now.isoformat(),
            previous_run_at=previous.isoformat() if previous is not None else None,
        )
