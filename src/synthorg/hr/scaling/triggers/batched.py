"""Batched (time-interval) scaling trigger.

Fires when enough time has passed since the last scaling
evaluation cycle.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_SCALING_TRIGGER_REQUESTED,
    HR_SCALING_TRIGGER_SKIPPED,
)

logger = get_logger(__name__)
_DEFAULT_INTERVAL_SECONDS: Final[int] = 900


class BatchedScalingTrigger:
    """Trigger that fires on a time interval.

    Tracks the last evaluation time and triggers when the
    configured interval has elapsed.

    Args:
        interval_seconds: Minimum seconds between evaluations.
    """

    def __init__(
        self,
        *,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._interval = max(1, interval_seconds)
        self._last_run: datetime | None = None
        self._in_progress = False
        self._lock = asyncio.Lock()

    @property
    def name(self) -> NotBlankStr:
        """Trigger name."""
        return NotBlankStr("batched")

    async def should_trigger(self) -> bool:
        """Trigger if the interval has elapsed since last run.

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        async with self._lock:
            if self._in_progress:
                logger.debug(
                    HR_SCALING_TRIGGER_SKIPPED,
                    trigger="batched",
                    reason="run_in_progress",
                )
                return False

            now = datetime.now(UTC)
            if self._last_run is not None:
                elapsed = (now - self._last_run).total_seconds()
                if elapsed < self._interval:
                    logger.debug(
                        HR_SCALING_TRIGGER_SKIPPED,
                        trigger="batched",
                        reason="interval_not_elapsed",
                        elapsed_seconds=int(elapsed),
                        interval_seconds=self._interval,
                    )
                    return False

            self._in_progress = True
            logger.debug(
                HR_SCALING_TRIGGER_REQUESTED,
                trigger="batched",
            )
            return True

    async def record_run(self) -> None:
        """Record that an evaluation cycle completed.

        Must be called under the same event loop as ``should_trigger``
        so the lock protects both the read and write of ``_last_run``.
        """
        async with self._lock:
            self._last_run = datetime.now(UTC)
            self._in_progress = False
