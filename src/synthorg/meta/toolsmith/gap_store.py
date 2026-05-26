"""In-memory capability-gap store.

Ring-buffered, in-process store recording every observation of a missing
capability (an MCP ``capability_gap`` envelope, an unknown-tool request,
or an explicit agent-reported gap). The toolsmith service queries
:meth:`recurring` to decide when a gap has recurred often enough to
warrant authoring a new tool.

Mirrors the established
:class:`~synthorg.meta.evolution.outcome_store.InMemoryEvolutionOutcomeStore`
ring-buffer pattern; a durable backend can ship behind the same
:class:`~synthorg.meta.toolsmith.protocol.CapabilityGapStore` protocol.
"""

import asyncio
from collections import deque
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.models import CapabilityGap
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_GAP_EVICTED,
    TOOLSMITH_GAP_RECORD_FAILED,
    TOOLSMITH_GAP_RECORDED,
)

if TYPE_CHECKING:
    from datetime import datetime, timedelta

logger = get_logger(__name__)


class _Observation:
    """A single (signature, timestamp) gap observation."""

    __slots__ = ("occurred_at", "signature")

    def __init__(self, signature: str, occurred_at: datetime) -> None:
        self.signature = signature
        self.occurred_at = occurred_at


class RingBufferCapabilityGapStore:
    """Process-local ring buffer of capability-gap observations.

    Args:
        max_observations: Ring-buffer capacity. Oldest entries are
            evicted when the buffer is full.
    """

    def __init__(self, *, max_observations: int) -> None:
        if max_observations < 1:
            msg = f"max_observations must be >= 1, got {max_observations}"
            raise ValueError(msg)
        self._max = max_observations
        self._obs: deque[_Observation] = deque(maxlen=max_observations)
        self._lock = asyncio.Lock()

    async def record_gap(
        self,
        signature: NotBlankStr,
        *,
        occurred_at: datetime,
    ) -> None:
        """Record one observation of a missing capability.

        Best-effort: swallows all exceptions except ``MemoryError`` /
        ``RecursionError`` so a recording failure never blocks the
        request path that produced the gap. A naive timestamp is dropped
        (logged) rather than raised, for the same reason.
        """
        if occurred_at.tzinfo is None:
            logger.warning(
                TOOLSMITH_GAP_RECORD_FAILED,
                signature=signature,
                error="occurred_at must be timezone-aware",
            )
            return
        try:
            async with self._lock:
                evicted = len(self._obs) == self._max
                self._obs.append(_Observation(str(signature), occurred_at))
            logger.debug(TOOLSMITH_GAP_RECORDED, signature=signature)
            if evicted:
                logger.info(TOOLSMITH_GAP_EVICTED, max_observations=self._max)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_GAP_RECORD_FAILED,
                signature=signature,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def recurring(
        self,
        *,
        threshold: int,
        window: timedelta,
        now: datetime,
    ) -> tuple[CapabilityGap, ...]:
        """Return gaps observed at least ``threshold`` times in the window.

        Args:
            threshold: Minimum occurrences within the window to qualify.
            window: Sliding window measured back from ``now``.
            now: Current time (UTC, caller-supplied via the Clock seam).

        Returns:
            Qualifying gaps, most-frequent first; ties broken by
            signature ascending for determinism.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if threshold < 1:
            msg = f"threshold must be >= 1, got {threshold}"
            raise ValueError(msg)
        if now.tzinfo is None:
            msg = "now must be timezone-aware"
            raise ValueError(msg)
        cutoff = now - window
        async with self._lock:
            snapshot = [o for o in self._obs if o.occurred_at >= cutoff]
        grouped: dict[str, list[datetime]] = {}
        for obs in snapshot:
            grouped.setdefault(obs.signature, []).append(obs.occurred_at)
        gaps = [
            CapabilityGap(
                signature=NotBlankStr(signature),
                occurrences=len(times),
                first_seen=min(times),
                last_seen=max(times),
            )
            for signature, times in grouped.items()
            if len(times) >= threshold
        ]
        gaps.sort(key=lambda g: (-g.occurrences, g.signature))
        return tuple(gaps)

    async def count(self) -> int:
        """Return current buffer size (not capacity).

        Returns:
            Resulting integer.
        """
        async with self._lock:
            return len(self._obs)

    async def clear(self) -> None:
        """Drop all stored observations."""
        async with self._lock:
            self._obs.clear()


__all__ = ["RingBufferCapabilityGapStore"]
