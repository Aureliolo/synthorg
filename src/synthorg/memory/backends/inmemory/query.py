"""Query-matching and expiry helpers for the in-memory memory backend.

Pure predicates over ``MemoryEntry`` extracted from ``adapter.py`` so the
backend class itself stays focused on CRUD orchestration and locking.
"""

from datetime import datetime

from synthorg.memory.models import MemoryEntry, MemoryQuery


def prune_expired(store: dict[str, MemoryEntry], now: datetime) -> None:
    """Remove expired entries from an agent store in-place (clock injected)."""
    expired = [mid for mid, entry in store.items() if is_expired(entry, now)]
    for mid in expired:
        del store[mid]


def is_expired(entry: MemoryEntry, now: datetime) -> bool:
    """Return True if *entry* has expired.

    Returns:
        ``True`` when the predicate holds, ``False`` otherwise.
    """
    return entry.expires_at is not None and entry.expires_at <= now


def matches_metadata(entry: MemoryEntry, query: MemoryQuery) -> bool:
    """Check namespace, category, tag, and text filters.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    if query.namespaces and entry.namespace not in query.namespaces:
        return False
    if query.categories and entry.category not in query.categories:
        return False
    if query.tags and not all(tag in entry.metadata.tags for tag in query.tags):
        return False
    return not (query.text and query.text.lower() not in entry.content.lower())


def matches(
    entry: MemoryEntry,
    query: MemoryQuery,
    now: datetime,
) -> bool:
    """Return True if *entry* passes all query filters.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    if is_expired(entry, now):
        return False
    if not matches_metadata(entry, query):
        return False
    if query.since is not None and entry.created_at < query.since:
        return False
    return not (query.until is not None and entry.created_at >= query.until)
