"""Module-level helpers for ``tool_retriever``.

Shared formatting, argument parsing, and result merging utilities used
by ``ToolBasedInjectionStrategy`` and its reformulation loop.
"""

from typing import TYPE_CHECKING, Any

from synthorg.core.enums import MemoryCategory
from synthorg.observability import get_logger
from synthorg.observability.events.memory import MEMORY_RETRIEVAL_DEGRADED

if TYPE_CHECKING:
    from synthorg.memory.models import MemoryEntry

logger = get_logger(__name__)


def _format_entries(entries: tuple[MemoryEntry, ...]) -> str:
    """Format memory entries as human-readable text.

    Returns:
        Result of type ``str``.
    """
    if not entries:
        return "No memories found."
    parts: list[str] = []
    for entry in entries:
        score = (
            f" (relevance: {entry.relevance_score:.2f})"
            if entry.relevance_score is not None
            else ""
        )
        parts.append(f"[{entry.category.value}]{score} {entry.content}")
    return "\n".join(parts)


def _parse_categories(
    raw: Any,
    *,
    agent_id: str | None = None,
) -> tuple[frozenset[MemoryCategory] | None, tuple[str, ...]]:
    """Parse category filter from LLM arguments.

    Invalid category values are logged at WARNING (so operators can
    see the agent's hallucinated categories) and returned in the
    rejected tuple so callers can surface them back to the LLM for
    self-correction.

    Malformed shapes (e.g. a bare string like ``"episodic"`` instead
    of ``["episodic"]``) are treated the same way: the raw value is
    returned in ``rejected_values`` and parsed_categories is ``None``
    so the search does NOT silently broaden to all categories.

    Args:
        raw: Raw value from tool arguments (expected list[str]).
        agent_id: Optional agent identifier for log context.

    Returns:
        Tuple of ``(parsed_categories, rejected_values)``.
        ``parsed_categories`` is ``None`` when input is absent.  When
        the input is present but malformed, ``rejected_values`` carries
        the raw value so callers can surface it.
    """
    if raw is None:
        return None, ()
    if not isinstance(raw, list):
        malformed = str(raw)
        logger.warning(
            MEMORY_RETRIEVAL_DEGRADED,
            source="category_parse",
            agent_id=agent_id,
            invalid_category=malformed,
            reason="categories must be a list, surfaced for self-correction",
        )
        return None, (malformed,)
    if not raw:
        return None, ()
    categories: list[MemoryCategory] = []
    rejected: list[str] = []
    for val in raw:
        try:
            categories.append(MemoryCategory(val))
        except ValueError:
            rejected_value = str(val)
            rejected.append(rejected_value)
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source="category_parse",
                agent_id=agent_id,
                invalid_category=rejected_value,
                reason="unknown category value, surfaced for self-correction",
            )
    parsed = frozenset(categories) if categories else None
    return parsed, tuple(rejected)


def merge_results(
    existing: tuple[MemoryEntry, ...],
    new: tuple[MemoryEntry, ...],
) -> tuple[MemoryEntry, ...]:
    """Merge two entry tuples by ID and re-sort by relevance.

    De-duplicates by ``entry.id``, keeping the higher ``relevance_score``
    copy when the same id appears in both inputs (``None`` treated as
    ``0.0``).  The returned tuple is sorted by relevance descending so
    that later reformulation rounds can actually surface better matches
    even when the first round already hit the tool's ``limit`` -- if we
    preserved first-round order, later unseen results would always land
    past the final truncation and Search-and-Ask would have no effect.
    Ties are broken by first-seen order for determinism.

    Args:
        existing: Current entries.
        new: New entries to merge in.

    Returns:
        Merged tuple sorted by relevance (highest first).
    """

    def _rel(entry: MemoryEntry) -> float:
        """Rel.

        Returns:
            Result of type ``float``.
        """
        return entry.relevance_score if entry.relevance_score is not None else 0.0

    merged: dict[str, MemoryEntry] = {}
    first_seen: dict[str, int] = {}
    for idx, entry in enumerate(existing):
        merged[entry.id] = entry
        first_seen.setdefault(entry.id, idx)

    offset = len(existing)
    for idx, entry in enumerate(new):
        if entry.id in merged:
            if _rel(entry) > _rel(merged[entry.id]):
                merged[entry.id] = entry
            continue
        merged[entry.id] = entry
        first_seen[entry.id] = offset + idx

    return tuple(
        sorted(
            merged.values(),
            key=lambda e: (-_rel(e), first_seen[e.id]),
        )
    )


def _truncate_entries(
    entries: tuple[MemoryEntry, ...],
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Truncate a cumulative result list to the caller-requested limit.

    The Search-and-Ask loop can accumulate more than ``limit`` entries
    when later rounds add unseen results; the tool contract promises
    ``limit`` entries, so truncate on return regardless of how many
    rounds ran.

    Returns:
        Tuple of ``MemoryEntry``.
    """
    if limit < 1 or len(entries) <= limit:
        return entries
    return entries[:limit]


def _parse_search_args(
    arguments: dict[str, Any],
    config_max_memories: int,
    *,
    agent_id: str | None = None,
) -> tuple[str | None, int, frozenset[MemoryCategory] | None, tuple[str, ...]]:
    """Extract and validate search_memory arguments.

    Args:
        arguments: Raw tool arguments from LLM.
        config_max_memories: System-configured max memories limit.
        agent_id: Optional agent identifier for log context.

    Returns:
        Tuple of ``(query_text, limit, categories, rejected_categories)``.
        ``query_text`` is ``None`` when the query is empty or
        whitespace-only.  ``rejected_categories`` contains raw values
        that failed to parse as ``MemoryCategory`` so the caller can
        surface them back to the LLM for self-correction.
    """
    query_raw = arguments.get("query", "")
    if not isinstance(query_raw, str):
        return None, 0, None, ()
    query_text = query_raw.strip()
    if not query_text:
        return None, 0, None, ()

    limit_raw = arguments.get("limit", 10)
    if isinstance(limit_raw, bool) or not isinstance(limit_raw, int | float):
        limit = 10
    else:
        limit = int(limit_raw)
    effective_max = min(50, config_max_memories)
    limit = min(max(limit, 1), effective_max)

    categories, rejected = _parse_categories(
        arguments.get("categories"),
        agent_id=agent_id,
    )
    return query_text, limit, categories, rejected
