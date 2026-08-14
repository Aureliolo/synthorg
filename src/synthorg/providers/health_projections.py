# module-kind: code
"""Projecting a snapshot of call outcomes into the views callers ask for.

Split from :mod:`synthorg.providers.health_tracker`, which owns the part
with state: the lock, the memory bound and the prune. Everything here is
a pure function of a record sequence and a reference time, so a view can
be exercised on a hand-built list without standing up a tracker, and the
tracker is left doing one job.

The split matters beyond tidiness. Every projection walks a full
snapshot, so which of them a caller reaches for is a cost decision:
asking for one agent's records is a pass over the store, and a roster
page asking per row pays that pass per row. Keeping them side by side
here is what makes the per-row version visibly the wrong one.
"""

import heapq
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType

from synthorg.providers.health import (
    HEALTH_WINDOW_HOURS,
    LIVENESS_SAMPLE_SIZE,
    ProviderHealthRecord,
    ProviderHealthSummary,
    RecordSource,
    aggregate_records,
)
from synthorg.providers.serviceability import (
    DEFAULT_THRESHOLDS,
    ModelServiceability,
    ServiceabilityThresholds,
    aggregate_serviceability,
)


def _pair_sort_key(
    item: tuple[tuple[str, str | None], list[ProviderHealthRecord]],
) -> tuple[str, str]:
    """Order pairs deterministically, unnamed models last within a provider.

    Returns:
        A total-order key over ``(provider, model)`` that tolerates the
        ``None`` model a provider-wide record carries.
    """
    provider, model = item[0]
    return provider, model or ""


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


def _within_daily_window(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
) -> list[ProviderHealthRecord]:
    """Return the records inside the 24-hour health window.

    Returns:
        The records at or after the cutoff and no later than *now*.
    """
    cutoff = now - timedelta(hours=HEALTH_WINDOW_HOURS)
    return [r for r in records if cutoff <= r.timestamp <= now]


def summary_for_provider(
    records: Sequence[ProviderHealthRecord],
    *,
    provider_name: str,
    now: datetime,
    epoch: datetime | None = None,
) -> ProviderHealthSummary:
    """Aggregate one provider's health over the 24-hour window.

    Args:
        records: The snapshot to project.
        provider_name: Provider to summarise.
        now: Reference time for the 24-hour window.
        epoch: Cutoff set by the last operator recheck, before which
            outcomes no longer count as evidence that the provider serves.

    Returns:
        The summary, or an empty one when nothing recent matched.
    """
    recent = [
        r
        for r in _within_daily_window(records, now=now)
        if r.provider_name == provider_name
    ]
    if not recent:
        return ProviderHealthSummary()
    return aggregate_records(
        recent,
        liveness_records=_liveness_slice(recent, epoch),
    )


def summaries_by_provider(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
    epochs: Mapping[str, datetime] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Mapping[str, ProviderHealthSummary]:
    """Aggregate every provider's health, optionally paginated.

    Args:
        records: The snapshot to project.
        now: Reference time for the 24-hour window.
        epochs: Per-provider recheck cutoffs, before which outcomes no
            longer count as evidence that the provider serves.
        limit: Maximum providers to include; ``None`` is unbounded, which
            is the contract callers needing every provider rely on (the
            readiness probe among them).
        offset: Page offset, honoured only when ``limit`` is set.

    Returns:
        Immutable mapping of provider name to summary.
    """
    cutoffs = epochs or {}
    by_provider: dict[str, list[ProviderHealthRecord]] = defaultdict(list)
    for record in _within_daily_window(records, now=now):
        by_provider[record.provider_name].append(record)
    items = sorted(by_provider.items())
    if limit is not None:
        offset = max(0, offset)
        items = items[offset : offset + max(0, limit)]
    return MappingProxyType(
        {
            name: aggregate_records(
                group,
                liveness_records=_liveness_slice(group, cutoffs.get(name)),
            )
            for name, group in items
        }
    )


def count_providers(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
) -> int:
    """Count providers with records inside the 24-hour window.

    Returns:
        The number of distinct providers, which is the total a paginated
        controller reports alongside its page.
    """
    return len({r.provider_name for r in _within_daily_window(records, now=now)})


def serviceability_for_pair(
    records: Sequence[ProviderHealthRecord],
    *,
    provider_name: str,
    model: str | None,
    now: datetime,
    thresholds: ServiceabilityThresholds = DEFAULT_THRESHOLDS,
) -> ModelServiceability:
    """Summarise one pair's recent behaviour.

    Args:
        records: The snapshot to project.
        provider_name: Connection the calls went out on.
        model: Model to summarise; ``None`` aggregates every model on the
            connection, which answers "is anything working here" rather
            than "is this pair usable".
        now: Reference time the window is measured back from.
        thresholds: Verdict boundaries and the evidence floor.

    Returns:
        The pair's recent-window view, empty when nothing matched.
    """
    matching = [
        record
        for record in records
        if record.provider_name == provider_name
        and (model is None or record.model == model)
    ]
    return aggregate_serviceability(
        matching,
        now=now,
        thresholds=thresholds,
        provider_name=provider_name,
        model=model,
    )


def serviceability_by_pair(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
    thresholds: ServiceabilityThresholds = DEFAULT_THRESHOLDS,
) -> Mapping[tuple[str, str | None], ModelServiceability]:
    """Summarise every pair a real call has exercised.

    Probe traffic names no model, so it can put a provider on the health
    surface but never a pair on this one.

    Returns:
        Immutable mapping of ``(provider, model)`` to its recent view.
    """
    grouped: dict[tuple[str, str | None], list[ProviderHealthRecord]] = defaultdict(
        list
    )
    for record in records:
        if record.source is not RecordSource.REAL_CALL:
            continue
        grouped[record.provider_name, record.model].append(record)
    return MappingProxyType(
        {
            key: aggregate_serviceability(
                group,
                now=now,
                thresholds=thresholds,
                provider_name=key[0],
                model=key[1],
            )
            for key, group in sorted(grouped.items(), key=_pair_sort_key)
        }
    )


def _is_attributed_real_call(
    record: ProviderHealthRecord,
    *,
    now: datetime,
    cutoff: datetime,
) -> bool:
    """Whether a record counts towards an agent's own dispatch history.

    Returns:
        ``True`` for an in-window real call carrying an agent.
    """
    return (
        record.agent_id is not None
        and record.source is RecordSource.REAL_CALL
        and cutoff <= record.timestamp <= now
    )


def records_for_agent(
    records: Sequence[ProviderHealthRecord],
    *,
    agent_id: str,
    now: datetime,
) -> tuple[ProviderHealthRecord, ...]:
    """Return one agent's real calls inside the 24-hour window.

    A probe belongs to no agent, and an unattributed call belongs to no
    agent either rather than to a placeholder one.

    Returns:
        Matching records in the order they were recorded.
    """
    cutoff = now - timedelta(hours=HEALTH_WINDOW_HOURS)
    return tuple(
        record
        for record in records
        if record.agent_id == agent_id
        and _is_attributed_real_call(record, now=now, cutoff=cutoff)
    )


def records_by_agent(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
) -> Mapping[str, tuple[ProviderHealthRecord, ...]]:
    """Group the window's attributed real calls by agent, in one pass.

    Serves the roster-wide comparison. Asking per agent would walk the
    whole snapshot again for each one, so a page of N agents cost N
    passes over the same records to partition them once.

    Returns:
        Immutable mapping of agent id to that agent's records. An agent
        with no attributed calls is absent rather than empty; the caller
        knows its own roster.
    """
    cutoff = now - timedelta(hours=HEALTH_WINDOW_HOURS)
    grouped: dict[str, list[ProviderHealthRecord]] = defaultdict(list)
    for record in records:
        if not _is_attributed_real_call(record, now=now, cutoff=cutoff):
            continue
        # Narrowed by the guard above; the annotation cannot see through it.
        assert record.agent_id is not None  # noqa: S101
        grouped[record.agent_id].append(record)
    return MappingProxyType({k: tuple(v) for k, v in grouped.items()})


__all__ = [
    "count_providers",
    "records_by_agent",
    "records_for_agent",
    "serviceability_by_pair",
    "serviceability_for_pair",
    "summaries_by_provider",
    "summary_for_provider",
]
