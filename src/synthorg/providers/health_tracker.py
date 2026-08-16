# module-kind: code
"""In-memory store of provider call outcomes, aggregated on demand.

Sits beside :mod:`synthorg.providers.health`, which owns the vocabulary
this reads and writes: the record it is handed, the summary it derives,
and the status that summary reports. Everything here is the part with
state, a lock and a memory bound, kept apart so the shape of a health
verdict can be read without the machinery that accumulates one.

In-memory is right for almost all of it: the outcomes are high-volume,
they decay within minutes, and losing them on a restart costs one fresh
measurement. The exception is a latching failure, which is honoured over a
lookback longer than the rate window precisely so it does NOT decay, and
whose own reason text says it does not clear without an operator. A
restart is not an operator, so those are written through to
:class:`~synthorg.persistence.provider_latch_protocol.ProviderLatchRepository`
and read back at boot; see :mod:`synthorg.providers.latch`.
"""

import asyncio
from collections.abc import Mapping
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
from synthorg.persistence.provider_latch_protocol import ProviderLatchRepository
from synthorg.providers.health import (
    HEALTH_WINDOW_HOURS,
    ProviderHealthRecord,
    ProviderHealthSummary,
    ProviderReachability,
    worst_reachability,
)
from synthorg.providers.health_projections import (
    count_providers,
    records_by_agent,
    records_for_agent,
    serviceability_by_pair,
    serviceability_for_pair,
    summaries_by_provider,
    summary_for_provider,
)
from synthorg.providers.latch_durability import DurableLatches
from synthorg.providers.serviceability import (
    DEFAULT_THRESHOLDS,
    ModelServiceability,
    ServiceabilityThresholds,
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
        "_latches",
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
        # Bound after persistence connects, not at construction: the tracker
        # is built in the first boot phase, before there is a backend to
        # write to. Absent, latches behave exactly as they did before they
        # were durable, which is what an in-memory test run wants.
        self._latches: DurableLatches | None = None

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
        if self._latches is not None:
            # Outside the lock: the durable write is I/O, and holding the
            # lock across it would serialise every other recorder behind a
            # database round trip for an outcome already in the window.
            await self._latches.persist(record)

    def bind_latch_store(self, store: ProviderLatchRepository) -> None:
        """Attach the durable latch store once persistence is connected."""
        self._latches = DurableLatches(store)

    async def restore_latches(self, *, now: datetime | None = None) -> int:
        """Read outstanding latches back in through the ordinary record path.

        Called once, after :meth:`bind_latch_store`.

        Returns:
            How many latches came back still standing; zero when no durable
            store is bound.
        """
        if self._latches is None:
            return 0
        return await self._latches.restore(self.record, now=now)

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
        snapshot, epochs = await self._snapshot(now=ref)
        return summary_for_provider(
            snapshot,
            provider_name=provider_name,
            now=ref,
            epoch=epochs.get(provider_name),
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
        snapshot, epochs = await self._snapshot(now=ref)
        return summaries_by_provider(
            snapshot,
            now=ref,
            epochs=epochs,
            limit=limit,
            offset=offset,
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
        snapshot, _ = await self._snapshot(now=ref)
        return count_providers(snapshot, now=ref)

    async def get_serviceability(
        self,
        provider_name: str,
        model: str | None,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> ModelServiceability:
        """Summarise one ``(provider, model)`` pair's recent behaviour.

        Args:
            provider_name: Connection the calls went out on.
            model: Model to summarise; ``None`` aggregates every model on
                the connection, which answers "is anything working here"
                rather than "is this pair usable".
            now: Reference time; defaults to current UTC time.
            thresholds: Verdict boundaries; defaults to the registered ones.

        Returns:
            The pair's recent-window view, empty when nothing matched.
        """
        ref = now or datetime.now(UTC)
        snapshot, _ = await self._snapshot(now=ref)
        return serviceability_for_pair(
            snapshot,
            provider_name=provider_name,
            model=model,
            now=ref,
            thresholds=thresholds or DEFAULT_THRESHOLDS,
        )

    async def get_all_serviceability(
        self,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> Mapping[tuple[str, str | None], ModelServiceability]:
        """Summarise every ``(provider, model)`` pair with recent traffic.

        Only pairs a real call has exercised appear: a probe names no model,
        so it can put a provider on the health surface but never a pair on
        this one.

        Returns:
            Immutable mapping of ``(provider, model)`` to its recent view.
        """
        ref = now or datetime.now(UTC)
        snapshot, _ = await self._snapshot(now=ref)
        return serviceability_by_pair(
            snapshot,
            now=ref,
            thresholds=thresholds or DEFAULT_THRESHOLDS,
        )

    async def records_for_agent(
        self,
        agent_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[ProviderHealthRecord, ...]:
        """Return the real calls attributed to *agent_id* in the 24h window.

        Serves the per-agent comparison, which is only meaningful over real
        traffic: a probe belongs to no agent, and an unattributed call
        belongs to no agent either rather than to a placeholder one.

        Returns:
            Matching records, newest last.
        """
        ref = now or datetime.now(UTC)
        snapshot, _ = await self._snapshot(now=ref)
        return records_for_agent(snapshot, agent_id=agent_id, now=ref)

    async def records_by_agent(
        self,
        *,
        now: datetime | None = None,
    ) -> Mapping[str, tuple[ProviderHealthRecord, ...]]:
        """Group the window's attributed real calls by agent, in one pass.

        Serves the roster-wide comparison. Asking per agent would take a
        fresh snapshot each time (and, past the prune threshold, a prune
        with it) to walk the same records again, so a page of N agents
        cost N passes over the whole store to partition it once.

        Returns:
            Immutable mapping of agent id to that agent's records. An agent
            with no attributed calls is absent rather than empty; the
            caller knows its own roster.
        """
        ref = now or datetime.now(UTC)
        snapshot, _ = await self._snapshot(now=ref)
        return records_by_agent(snapshot, now=ref)

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
