"""Post-retrieval filters for Mem0 results.

Mem0 cannot natively express every ``MemoryQuery`` filter (expiry,
category, tag, time range, minimum relevance), so these predicates run
in-process over the entries Mem0 returns.  Stateless -- no I/O.
"""

from datetime import UTC, datetime

from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.observability import get_logger
from synthorg.observability.events.memory import MEMORY_FILTER_APPLIED

logger = get_logger(__name__)


def _is_expired(entry: MemoryEntry, now: datetime) -> bool:
    """Return True if *entry* has expired.

    Returns:
        ``True`` when the predicate holds, ``False`` otherwise.
    """
    return entry.expires_at is not None and entry.expires_at <= now


def _matches_metadata(entry: MemoryEntry, query: MemoryQuery) -> bool:
    """Check namespace, category, and tag filters.

    Returns:
        ``True`` if the entry matches the namespace/category/tag
        filters, ``False`` otherwise.
    """
    if query.namespaces and entry.namespace not in query.namespaces:
        return False
    if query.categories and entry.category not in query.categories:
        return False
    return not query.tags or all(tag in entry.metadata.tags for tag in query.tags)


def _matches_filters(
    entry: MemoryEntry,
    query: MemoryQuery,
    now: datetime,
) -> bool:
    """Return True if *entry* passes all query filters.

    Returns:
        ``True`` if the entry passes every query filter, ``False``
        otherwise.
    """
    if _is_expired(entry, now):
        return False
    if not _matches_metadata(entry, query):
        return False
    if query.since is not None and entry.created_at < query.since:
        return False
    if query.until is not None and entry.created_at >= query.until:
        return False
    return (
        query.min_relevance <= 0.0
        or entry.relevance_score is None
        or entry.relevance_score >= query.min_relevance
    )


def apply_post_filters(
    entries: tuple[MemoryEntry, ...],
    query: MemoryQuery,
) -> tuple[MemoryEntry, ...]:
    """Apply post-retrieval filters that Mem0 cannot handle natively.

    Filters expired entries, then applies category, tags, time range,
    and minimum relevance filters.  Entries with
    ``relevance_score=None`` (e.g. from ``get_all``) are never
    excluded by ``min_relevance`` -- the filter only applies when a
    score is present.

    Time range uses a half-open interval: entries with
    ``created_at >= since`` and ``created_at < until`` are included.

    Args:
        entries: Raw entries from Mem0.
        query: Original query with filter criteria.

    Returns:
        Filtered entries (order preserved).
    """
    now = datetime.now(UTC)
    pre_count = len(entries)
    result = [e for e in entries if _matches_filters(e, query, now)]
    post_count = len(result)
    if pre_count > 0 and post_count == 0:
        logger.warning(
            MEMORY_FILTER_APPLIED,
            field="post_filter",
            reason="all entries filtered out by post-filters",
            pre_filter_count=pre_count,
        )
    elif pre_count != post_count:
        logger.debug(
            MEMORY_FILTER_APPLIED,
            field="post_filter",
            pre_filter_count=pre_count,
            post_filter_count=post_count,
            reason="entries filtered by post-filters",
        )
    return tuple(result)
