# module-kind: code
"""In-memory store of provider call outcomes, aggregated on demand.

Sits beside :mod:`synthorg.providers.health`, which owns the vocabulary
this reads and writes: the record it is handed, the summary it derives,
and the status that summary reports. Everything here is the part with
state, a lock and a memory bound, kept apart so the shape of a health
verdict can be read without the machinery that accumulates one.
"""

import asyncio
import heapq
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_AUTO_PRUNED,
    PROVIDER_HEALTH_CLEARED,
    PROVIDER_HEALTH_LIVENESS_SUPERSEDED,
    PROVIDER_HEALTH_PRUNED,
    PROVIDER_REACHABILITY_DEGRADED,
)
from synthorg.persistence._shared import format_iso_utc
from synthorg.providers.health import (
    HEALTH_WINDOW_HOURS,
    LIVENESS_SAMPLE_SIZE,
    ProviderHealthRecord,
    ProviderHealthSummary,
    ProviderReachability,
    aggregate_records,
    worst_reachability,
)

logger = get_logger(__name__)

_AUTO_PRUNE_THRESHOLD: Final[int] = 100_000


def _liveness_slice(
    records: Sequence[ProviderHealthRecord],
    epoch: datetime | None,
) -> tuple[ProviderHealthRecord, ...]:
    """Pick the outcomes that decide whether a provider is serving.

    The newest :data:`LIVENESS_SAMPLE_SIZE` at or after *epoch*. Selected on
    the timestamp rather than by taking the tail of the list, because a
    caller is free to record an outcome it measured a moment ago after one
    it measured since, and the newest records are the whole point here.

    Args:
        records: This provider's records inside the 24h window.
        epoch: Cutoff set by the last operator recheck, or ``None`` when
            they have never rechecked this provider.

    Returns:
        The deciding outcomes, oldest first; empty when the epoch excludes
        everything, which reports ``UNKNOWN``.
    """
    eligible = (
        records if epoch is None else [r for r in records if r.timestamp >= epoch]
    )
    if not eligible:
        return ()
    newest = heapq.nlargest(LIVENESS_SAMPLE_SIZE, eligible, key=lambda r: r.timestamp)
    return tuple(reversed(newest))


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
        "_liveness_epoch",
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
        # Per provider, the moment before which outcomes no longer count as
        # evidence of whether it is serving. Only an operator sets one, by
        # rechecking; see :meth:`supersede_liveness`. A timestamp rather than
        # a deletion so the 24h reliability aggregate stays whole.
        self._liveness_epoch: dict[str, datetime] = {}

    def clear(self) -> None:
        """Reset all health records for test isolation."""
        cleared_count = len(self._records)
        self._records.clear()
        self._liveness_epoch.clear()
        self._appends_since_prune = 0
        logger.info(PROVIDER_HEALTH_CLEARED, cleared_count=cleared_count)

    async def supersede_liveness(self, provider_name: str, *, at: datetime) -> None:
        """Discard outcomes before *at* as evidence that *provider_name* serves.

        The operator's answer to "is the past still evidence?", which is a
        question only they can settle: they are the one who knows they just
        restarted the endpoint, replaced the key, or fixed the network.
        Without it a recheck is arithmetically incapable of clearing a
        verdict, since one fresh sample cannot outvote a window already full
        of failures, so a provider that is serving again reports DOWN for as
        long as those failures stay in the window.

        Reliability is untouched: no record is removed, so
        ``error_rate_percent_24h`` still reports the whole day including the
        outage. Only :attr:`ProviderHealthSummary.health_status` moves.

        Args:
            provider_name: Provider whose liveness evidence resets.
            at: Cutoff; outcomes at or after it still count.
        """
        async with self._lock:
            self._liveness_epoch[provider_name] = at
        # The one thing that can move a verdict without a call having
        # succeeded or failed, so a status that changed for no visible reason
        # is explained by this line and nothing else.
        logger.info(
            PROVIDER_HEALTH_LIVENESS_SUPERSEDED,
            provider=provider_name,
            cutoff=format_iso_utc(at),
        )

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

        snapshot, epochs = await self._snapshot(now=ref)
        recent = [
            r
            for r in snapshot
            if r.provider_name == provider_name and cutoff <= r.timestamp <= ref
        ]

        if not recent:
            return ProviderHealthSummary()

        return aggregate_records(
            recent,
            liveness_records=_liveness_slice(recent, epochs.get(provider_name)),
        )

    async def reachability(
        self,
        *,
        now: datetime | None = None,
    ) -> ProviderReachability:
        """Return the worst verdict across every tracked provider.

        Reported on the health surface, never used to gate traffic: a
        third-party outage is the same for every replica, so draining on it
        turns a degraded feature into a total one.

        Three states, because a boolean has to fold ``DEGRADED`` into one side
        or the other: folded into "reachable" it reports the same green for a
        provider failing two calls in five as for one failing none, and folded
        the other way it reports an outage for a provider that is serving.
        ``UNKNOWN`` (nothing has called it yet) reports ``OK`` so a fresh boot
        never claims trouble before the first call lands.

        Returns:
            The worst verdict present, or ``OK`` when nothing is tracked.
        """
        summaries = await self.get_all_summaries(now=now)
        verdict = worst_reachability(
            summary.health_status for summary in summaries.values()
        )
        if verdict is not ProviderReachability.OK:
            # The roll-up is one word for the whole fleet, so on its own it
            # says an operator has a problem without saying where.
            logger.info(
                PROVIDER_REACHABILITY_DEGRADED,
                verdict=verdict.value,
                providers=sorted(
                    name
                    for name, summary in summaries.items()
                    if worst_reachability((summary.health_status,)) is verdict
                ),
            )
        return verdict

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

        snapshot, epochs = await self._snapshot(now=ref)
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
            {
                name: aggregate_records(
                    records,
                    liveness_records=_liveness_slice(records, epochs.get(name)),
                )
                for name, records in items
            }
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
        snapshot, _ = await self._snapshot(now=ref)
        names = {r.provider_name for r in snapshot if cutoff <= r.timestamp <= ref}
        return len(names)

    async def _snapshot(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[tuple[ProviderHealthRecord, ...], Mapping[str, datetime]]:
        """Return an immutable snapshot of all current records and epochs.

        Both under one lock acquisition, because a summary reads them
        together: taking them separately lets a recheck land in between and
        produce a verdict from records the new epoch was meant to exclude.

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
            Immutable tuple of all current health records, paired with an
            immutable view over a copy of the per-provider liveness epochs.
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
            return tuple(self._records), MappingProxyType(dict(self._liveness_epoch))

    def _prune_before(self, cutoff: datetime) -> int:
        """Remove records older than *cutoff*.  Caller must hold ``_lock``.

        Returns:
            The number of records removed that were older than *cutoff*.
        """
        # Reset even on the empty-list shortcut: the pass ran, so the next
        # automatic one waits its full interval either way.
        self._appends_since_prune = 0
        self._prune_epochs_before(cutoff)
        if not self._records:
            return 0
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        return before - len(self._records)

    def _prune_epochs_before(self, cutoff: datetime) -> None:
        """Drop liveness epochs that can no longer exclude anything.

        An epoch older than the window is inert by then: every surviving
        record is at or after it, so it selects exactly what no epoch at all
        would. Records are bounded by the window, but epochs are keyed by
        provider name, so without this a process that outlives a few rounds
        of provider renames accumulates them for as long as it runs.
        """
        self._liveness_epoch = {
            name: at for name, at in self._liveness_epoch.items() if at >= cutoff
        }


__all__ = ["ProviderHealthTracker"]
