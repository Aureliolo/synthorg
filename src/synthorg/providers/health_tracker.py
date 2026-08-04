# module-kind: code
"""In-memory store of provider call outcomes, aggregated on demand.

Sits beside :mod:`synthorg.providers.health`, which owns the vocabulary
this reads and writes: the record it is handed, the summary it derives,
and the status that summary reports. Everything here is the part with
state, a lock and a memory bound, kept apart so the shape of a health
verdict can be read without the machinery that accumulates one.
"""

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_AUTO_PRUNED,
    PROVIDER_HEALTH_CLEARED,
    PROVIDER_HEALTH_PRUNED,
)
from synthorg.providers.health import (
    HEALTH_WINDOW_HOURS,
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderHealthSummary,
    aggregate_records,
)

logger = get_logger(__name__)

_AUTO_PRUNE_THRESHOLD: Final[int] = 100_000


class ProviderHealthTracker:
    """In-memory tracker for provider call outcomes with TTL-based eviction.

    Concurrency-safe via ``asyncio.Lock``.  Follows the same
    TTL-based eviction pattern as
    :class:`~synthorg.budget.tracker.CostTracker`: memory is bounded by
    a soft auto-prune that removes records older than 24 hours once the
    record count exceeds *auto_prune_threshold*. On the write path that
    pass is additionally spaced to one per threshold's worth of appends
    (see :meth:`_write_prune_is_due`).

    Args:
        auto_prune_threshold: Record count above which auto-pruning is
            attempted, and the write path's minimum spacing between
            attempts.  Defaults to 100,000.

    Raises:
        ValueError: If *auto_prune_threshold* < 1.
    """

    __slots__ = (
        "_appends_since_prune",
        "_auto_prune_threshold",
        "_lock",
        "_records",
    )

    def __init__(
        self,
        *,
        auto_prune_threshold: int = _AUTO_PRUNE_THRESHOLD,
    ) -> None:
        if auto_prune_threshold < 1:
            msg = f"auto_prune_threshold must be >= 1, got {auto_prune_threshold}"
            raise ValueError(msg)
        self._records: list[ProviderHealthRecord] = []
        self._lock = asyncio.Lock()
        self._auto_prune_threshold = auto_prune_threshold
        self._appends_since_prune = 0

    def clear(self) -> None:
        """Reset all health records for test isolation."""
        cleared_count = len(self._records)
        self._records.clear()
        self._appends_since_prune = 0
        logger.info(PROVIDER_HEALTH_CLEARED, cleared_count=cleared_count)

    def _write_prune_is_due(self) -> bool:
        """Whether the write path has earned another rebuild.

        Size alone is the wrong trigger here once the window itself is
        bigger than the threshold: every record thereafter finds the list
        over the line, and the rebuild that follows frees nothing, so a
        full pass lands on every single call while reclaiming nothing.
        Real completion traffic feeds this tracker, so that state is
        reached by ordinary load rather than by abuse.

        Requiring a threshold's worth of new records between attempts
        keeps the write path amortised at one pass per threshold appends,
        and leaves the memory bound at the window's own size plus at most
        that much slack. Nothing can shrink it below the window: a
        24-hour aggregate is computed from 24 hours of records. The read
        path needs no such spacing, because it runs once per reader
        rather than once per recorded call.

        Returns:
            True when a rebuild should be attempted now.
        """
        return (
            len(self._records) > self._auto_prune_threshold
            and self._appends_since_prune >= self._auto_prune_threshold
        )

    async def record(self, record: ProviderHealthRecord) -> None:
        """Append a health record, reclaiming the window on the way past.

        Every aggregate is computed over the trailing 24 hours, so a record
        older than that can never be read again and only costs memory.
        Pruning here rather than only on read is what bounds the list: a
        deployment that writes far more often than it reads (a connection
        test, an on-demand recheck and the periodic sweep all land here)
        would otherwise grow until something happened to ask for a summary.

        Rate-limited by :meth:`_write_prune_is_due`, because the prune
        rebuilds the list: running it on every append would make a sweep
        across N providers quadratic in the records it just wrote.

        Args:
            record: Immutable call outcome record.
        """
        async with self._lock:
            self._records.append(record)
            self._appends_since_prune += 1
            if self._write_prune_is_due():
                cutoff = record.timestamp - timedelta(hours=HEALTH_WINDOW_HOURS)
                pruned = self._prune_before(cutoff)
                if pruned:
                    logger.debug(
                        PROVIDER_HEALTH_PRUNED,
                        pruned=pruned,
                        remaining=len(self._records),
                    )

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        """Remove records older than the 24-hour health window.

        Call periodically from long-running services to bound
        memory growth.

        Args:
            now: Reference time.  Defaults to current UTC time.

        Returns:
            Number of records removed.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=HEALTH_WINDOW_HOURS)
        async with self._lock:
            pruned = self._prune_before(cutoff)
            if pruned:
                logger.info(
                    PROVIDER_HEALTH_PRUNED,
                    pruned=pruned,
                    remaining=len(self._records),
                )
            return pruned

    async def get_summary(
        self,
        provider_name: str,
        *,
        now: datetime | None = None,
    ) -> ProviderHealthSummary:
        """Build an aggregated health summary for a provider.

        Only considers records within the last 24 hours.

        Args:
            provider_name: Provider to summarise.
            now: Reference time for the 24h window.  Defaults to
                current UTC time.

        Returns:
            Aggregated health summary.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=HEALTH_WINDOW_HOURS)

        snapshot = await self._snapshot(now=ref)
        recent = [
            r
            for r in snapshot
            if r.provider_name == provider_name and cutoff <= r.timestamp <= ref
        ]

        if not recent:
            return ProviderHealthSummary()

        return aggregate_records(recent)

    async def are_all_reachable(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return True when no tracked provider is currently DOWN.

        Reported on the health surface, never used to gate traffic:
        a third-party outage is the same for every replica, so draining
        on it turns a degraded feature into a total one. Providers whose
        recent call window contains too many failures derive a
        :attr:`ProviderHealthStatus.DOWN` status; any single one of
        those flips the reachability bit. ``DEGRADED`` providers stay
        reachable because partial traffic is preferable to a full
        outage; ``UNKNOWN`` (no recent calls) is also treated as
        reachable so a fresh boot never reports unreachable before the
        first provider call lands.
        """
        summaries = await self.get_all_summaries(now=now)
        return not any(
            summary.health_status is ProviderHealthStatus.DOWN
            for summary in summaries.values()
        )

    async def get_all_summaries(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Mapping[str, ProviderHealthSummary]:
        """Build summaries for all known providers, optionally paginated.

        Args:
            now: Reference time for the 24h window.
            limit: Maximum providers to include (``None`` for unbounded;
                preserves the historical contract used by callers that
                need every provider's status, e.g. the readiness probe).
            offset: Page offset honoured only when ``limit`` is set.

        Returns:
            Immutable mapping of provider name to health summary,
            wrapped in :class:`types.MappingProxyType` so callers
            cannot mutate the aggregate view.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=HEALTH_WINDOW_HOURS)

        snapshot = await self._snapshot(now=ref)
        by_provider: dict[str, list[ProviderHealthRecord]] = defaultdict(list)
        for r in snapshot:
            if cutoff <= r.timestamp <= ref:
                by_provider[r.provider_name].append(r)

        items = sorted(by_provider.items())
        if limit is not None:
            offset = max(0, offset)
            end = offset + max(0, limit)
            items = items[offset:end]
        return MappingProxyType(
            {name: aggregate_records(records) for name, records in items}
        )

    async def count_all_summaries(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Return the count of providers with records inside the 24h window.

        Companion to :meth:`get_all_summaries` for paginated controllers
        that need a total alongside the page.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=HEALTH_WINDOW_HOURS)
        snapshot = await self._snapshot(now=ref)
        names = {r.provider_name for r in snapshot if cutoff <= r.timestamp <= ref}
        return len(names)

    async def _snapshot(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[ProviderHealthRecord, ...]:
        """Return an immutable snapshot of all current records.

        When the record count exceeds the auto-prune threshold, expired
        records are removed before the snapshot is taken. Unspaced,
        unlike the write path: a read happens once per reader, so a pass
        here cannot compound the way one per recorded call does, and this
        is the only prune that gets to use the reader's own reference
        time rather than a record's.

        Args:
            now: Reference time for auto-prune cutoff.  Defaults to
                current UTC time.

        Returns:
            Immutable tuple of all current health records.
        """
        async with self._lock:
            if len(self._records) > self._auto_prune_threshold:
                ref = now or datetime.now(UTC)
                cutoff = ref - timedelta(hours=HEALTH_WINDOW_HOURS)
                pruned = self._prune_before(cutoff)
                if pruned:
                    logger.info(
                        PROVIDER_HEALTH_AUTO_PRUNED,
                        pruned=pruned,
                        remaining=len(self._records),
                    )
            return tuple(self._records)

    def _prune_before(self, cutoff: datetime) -> int:
        """Remove records older than *cutoff*.  Caller must hold ``_lock``.

        Returns:
            The number of records removed that were older than *cutoff*.
        """
        # Reset even on the empty-list shortcut: the pass ran, so the next
        # automatic one waits its full interval either way.
        self._appends_since_prune = 0
        if not self._records:
            return 0
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        return before - len(self._records)


__all__ = ["ProviderHealthTracker"]
