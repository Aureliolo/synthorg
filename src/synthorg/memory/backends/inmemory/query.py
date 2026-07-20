"""Query-matching and expiry helpers for the in-memory memory backend.

Pure predicates over ``MemoryEntry`` extracted from ``adapter.py`` so the
backend class itself stays focused on CRUD orchestration and locking.
"""

from datetime import datetime

from synthorg.memory.bm25 import tokenize_for_index
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.write_gate import is_superseded


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


def text_overlap_score(entry: MemoryEntry, text: str) -> float:
    """Score *entry* by the share of *text*'s terms it contains.

    Term overlap rather than whole-string containment: recall queries
    compose task, role, department and project context, so a query is
    almost never a literal substring of a stored memory. Requiring that
    would make this backend recall nothing while appearing to work.

    Returning a score rather than a boolean matters for precision. Bare
    any-term overlap retrieves anything sharing one incidental word, so
    the score lets the ranking pipeline's configured ``min_relevance``
    do the gating instead of a second threshold invented here.

    Tokenisation is the same one the durable backend indexes with, so
    the two agree on what counts as a term.

    Returns:
        The fraction of query terms present in the entry, in [0, 1]. A
        query with no meaningful terms constrains nothing and scores 1.
    """
    query_terms = set(tokenize_for_index(text))
    if not query_terms:
        return 1.0
    shared = query_terms & set(tokenize_for_index(entry.content))
    return len(shared) / len(query_terms)


def matches_non_text(entry: MemoryEntry, query: MemoryQuery) -> bool:
    """Check every metadata filter except the text-overlap one.

    Split from the text check so a caller that also needs the overlap
    *score* (retrieval) computes it once rather than here and again when
    ranking.

    Returns:
        ``True`` when the entry passes every non-text filter.
    """
    if query.namespaces and entry.namespace not in query.namespaces:
        return False
    if query.categories and entry.category not in query.categories:
        return False
    if query.tags and not all(tag in entry.metadata.tags for tag in query.tags):
        return False
    return query.include_superseded or not is_superseded(entry)


def matches_metadata(entry: MemoryEntry, query: MemoryQuery) -> bool:
    """Check namespace, category, tag, superseded, and text filters.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    if not matches_non_text(entry, query):
        return False
    return not (query.text and text_overlap_score(entry, query.text) <= 0.0)


def matches_window(entry: MemoryEntry, query: MemoryQuery, now: datetime) -> bool:
    """Check expiry and the since/until time window.

    Returns:
        ``True`` when the entry is live and inside the window.
    """
    if is_expired(entry, now):
        return False
    if query.since is not None and entry.created_at < query.since:
        return False
    return not (query.until is not None and entry.created_at >= query.until)


def matches(
    entry: MemoryEntry,
    query: MemoryQuery,
    now: datetime,
) -> bool:
    """Return True if *entry* passes all query filters.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    return matches_window(entry, query, now) and matches_metadata(entry, query)
