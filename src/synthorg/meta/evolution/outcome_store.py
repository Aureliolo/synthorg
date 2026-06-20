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

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
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
        clock: Clock seam for the ``recorded_at`` stamp built in
            :meth:`record`; tests inject a ``FakeClock``.
    """

    def __init__(
        self,
        *,
        max_results: int = _DEFAULT_MAX_RESULTS,
        clock: Clock | None = None,
    ) -> None:
        if max_results < 1:
            msg = f"max_results must be >= 1, got {max_results}"
            raise ValueError(msg)
        self._max_results = max_results
        self._records: deque[EvolutionOutcomeRecord] = deque(maxlen=max_results)
        self._lock = asyncio.Lock()
        self._clock: Clock = clock if clock is not None else SystemClock()

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
            record = EvolutionOutcomeRecord(
                agent_id=agent_id,
                axis=axis,
                applied=applied,
                proposed_at=proposed_at,
                recorded_at=self._clock.now(),
            )
            await self.ingest(record)
            logger.debug(
                EVOLUTION_OUTCOME_RECORDED,
                agent_id=agent_id,
                axis=axis,
                applied=applied,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_OUTCOME_RECORD_FAILED,
                agent_id=agent_id,
                axis=axis,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def ingest(self, record: EvolutionOutcomeRecord) -> None:
        """Append a pre-built outcome record to the ring buffer.

        Shared by :meth:`record` (after building the record from kwargs)
        and the durable write-through / rehydrate paths, which already
        hold a fully-formed :class:`EvolutionOutcomeRecord` and need a
        consistent ``recorded_at`` across the buffer and the durable log.
        """
        async with self._lock:
            evicted = len(self._records) == self._max_results
            self._records.append(record)
            # Logged under the lock so two concurrent ingests cannot both
            # observe a full buffer and double-count the single eviction.
            if evicted:
                logger.info(
                    EVOLUTION_OUTCOME_STORE_EVICTED,
                    max_results=self._max_results,
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
        return roll_up_outcomes(records, max_recent=max_recent)

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


def roll_up_outcomes(
    records: tuple[EvolutionOutcomeRecord, ...],
    *,
    max_recent: int = _DEFAULT_MAX_RECENT,
) -> OrgEvolutionSummary:
    """Roll a newest-first record tuple into an :class:`OrgEvolutionSummary`.

    Shared by the in-memory ring-buffer summary and the durable
    read-service window summary so both compute approval rate, the most
    adapted axis, and the recent-outcomes tail identically.

    Args:
        records: Outcome records, newest-first.
        max_recent: How many of the newest records to surface.

    Returns:
        The rolled-up summary; empty when ``records`` is empty.
    """
    if not records:
        return OrgEvolutionSummary()
    applied_count = sum(1 for r in records if r.applied)
    total = len(records)
    axis_counts: dict[str, int] = {}
    for record in records:
        axis_counts[record.axis] = axis_counts.get(record.axis, 0) + 1
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
        approval_rate=applied_count / total,
        most_adapted_axis=_pick_most_adapted(axis_counts),
    )


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
    "roll_up_outcomes",
]
