"""In-memory evolution outcome store.

Ring-buffered, in-process store recording the terminal outcome of
every improvement proposal the self-improvement cycle processes.
Implements the
:class:`~synthorg.meta.evolution.outcome_store_protocol.EvolutionOutcomeStore`
protocol.

The store owns the roll-up logic that turns records into an
:class:`OrgEvolutionSummary`; aggregators call :meth:`summarize`
rather than reimplementing counts, approval rates, or axis
distribution.
"""

import asyncio
from collections import deque
from datetime import datetime
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.meta.models import ProposalAltitude
from synthorg.meta.signal_models import EvolutionOutcomeSummary, OrgEvolutionSummary
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import (
    EVOLUTION_OUTCOME_RECORD_FAILED,
    EVOLUTION_OUTCOME_RECORDED,
    EVOLUTION_OUTCOME_STORE_EVICTED,
)

logger = get_logger(__name__)
_DEFAULT_MAX_RECENT: Final[int] = 10

_DEFAULT_MAX_RESULTS: Final[int] = 5_000
"""Default ring-buffer capacity.

The self-improvement cycle runs on a ceremony cadence (hours/days),
so 5k records covers months of history at typical proposal volumes.
Operators who need longer retention in memory can raise the cap via
the constructor; a durable backend behind the same protocol is the
right answer for multi-year retention.
"""


class InMemoryEvolutionOutcomeStore:
    """Process-local ring buffer of evolution outcome records.

    Args:
        max_results: Ring buffer capacity.  Oldest entries are evicted
            when the buffer is full.
    """

    def __init__(self, *, max_results: int = _DEFAULT_MAX_RESULTS) -> None:
        if max_results < 1:
            msg = f"max_results must be >= 1, got {max_results}"
            raise ValueError(msg)
        self._max_results = max_results
        self._records: deque[EvolutionOutcomeRecord] = deque(maxlen=max_results)
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        agent_id: NotBlankStr,
        axis: NotBlankStr,
        applied: bool,
        proposed_at: datetime,
    ) -> None:
        """Record a terminal outcome.

        Best-effort; swallows all exceptions except ``MemoryError`` /
        ``RecursionError`` so the self-improvement cycle is never
        blocked by a store failure.
        """
        try:
            altitude = ProposalAltitude(axis)
            record = EvolutionOutcomeRecord(
                agent_id=agent_id,
                axis=altitude,
                applied=applied,
                proposed_at=proposed_at,
            )
            async with self._lock:
                evicted = len(self._records) == self._max_results
                self._records.append(record)
            logger.debug(
                EVOLUTION_OUTCOME_RECORDED,
                agent_id=agent_id,
                axis=axis,
                applied=applied,
            )
            if evicted:
                logger.info(
                    EVOLUTION_OUTCOME_STORE_EVICTED,
                    max_results=self._max_results,
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_OUTCOME_RECORD_FAILED,
                agent_id=agent_id,
                axis=axis,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def query(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        """Return outcomes recorded within ``[since, until)``.

        Ordered newest-first.

        Returns:
            Tuple of the declared element types.
        """
        _validate_window(since, until)
        async with self._lock:
            snapshot = tuple(self._records)
        return tuple(
            reversed(
                [r for r in snapshot if since <= r.recorded_at < until],
            ),
        )

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
        max_recent: int = _DEFAULT_MAX_RECENT,
    ) -> OrgEvolutionSummary:
        """Roll recorded outcomes into an :class:`OrgEvolutionSummary`.

        Returns:
            ``OrgEvolutionSummary`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if max_recent < 1:
            msg = f"max_recent must be >= 1, got {max_recent}"
            raise ValueError(msg)
        records = await self.query(since=since, until=until)
        if not records:
            return OrgEvolutionSummary()

        applied_count = sum(1 for r in records if r.applied)
        total = len(records)
        approval_rate = applied_count / total

        axis_counts: dict[str, int] = {}
        for record in records:
            axis_counts[record.axis] = axis_counts.get(record.axis, 0) + 1
        most_adapted = _pick_most_adapted(axis_counts)

        recent = tuple(
            EvolutionOutcomeSummary(
                agent_id=r.agent_id,
                axis=r.axis,
                applied=r.applied,
                proposed_at=r.proposed_at,
            )
            for r in records[:max_recent]
        )

        return OrgEvolutionSummary(
            recent_outcomes=recent,
            total_proposals=total,
            approval_rate=approval_rate,
            most_adapted_axis=most_adapted,
        )

    async def count(self) -> int:
        """Return current buffer size (not capacity).

        Returns:
            Resulting integer.
        """
        async with self._lock:
            return len(self._records)

    async def clear(self) -> None:
        """Drop all stored records."""
        async with self._lock:
            self._records.clear()


def _validate_window(since: datetime, until: datetime) -> None:
    """Reject inverted or naive windows before any scan happens.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if since.tzinfo is None or until.tzinfo is None:
        msg = "since/until must be timezone-aware"
        raise ValueError(msg)
    if since >= until:
        msg = (
            f"since ({since.isoformat()}) must be earlier than until "
            f"({until.isoformat()})"
        )
        raise ValueError(msg)


def _pick_most_adapted(axis_counts: dict[str, int]) -> NotBlankStr | None:
    """Return the axis with the most outcomes.

    Ties broken alphabetically for determinism.

    Returns:
        The ``NotBlankStr`` value when present, ``None`` otherwise.
    """
    if not axis_counts:
        return None
    ranked = sorted(axis_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return NotBlankStr(ranked[0][0])


__all__ = [
    "InMemoryEvolutionOutcomeStore",
]
