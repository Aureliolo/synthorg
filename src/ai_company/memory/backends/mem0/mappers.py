"""Bidirectional mapping between SynthOrg domain models and Mem0 dicts.

Pure functions — no I/O, no side effects.  Each mapper handles one
direction of the conversion so the adapter stays thin.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ai_company.core.enums import MemoryCategory
from ai_company.core.types import NotBlankStr
from ai_company.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

# Metadata prefix avoids collisions with Mem0's own keys.
_PREFIX = "_synthorg_"


def build_mem0_metadata(request: MemoryStoreRequest) -> dict[str, Any]:
    """Serialize a store request's metadata into Mem0-compatible dict.

    Args:
        request: Memory store request with category and metadata.

    Returns:
        Dict of prefixed metadata fields for Mem0.
    """
    meta: dict[str, Any] = {
        f"{_PREFIX}category": request.category.value,
        f"{_PREFIX}confidence": request.metadata.confidence,
    }
    if request.metadata.source is not None:
        meta[f"{_PREFIX}source"] = request.metadata.source
    if request.metadata.tags:
        meta[f"{_PREFIX}tags"] = list(request.metadata.tags)
    if request.expires_at is not None:
        meta[f"{_PREFIX}expires_at"] = request.expires_at.isoformat()
    return meta


def store_request_to_mem0_args(
    agent_id: str,
    request: MemoryStoreRequest,
) -> dict[str, Any]:
    """Convert a store request to ``Memory.add()`` keyword arguments.

    Args:
        agent_id: Owning agent identifier.
        request: Memory store request.

    Returns:
        Dict of kwargs for ``Memory.add()``.
    """
    messages = [{"role": "user", "content": request.content}]
    metadata = build_mem0_metadata(request)
    return {
        "messages": messages,
        "user_id": agent_id,
        "metadata": metadata,
        "infer": False,
    }


def parse_mem0_datetime(raw: str | None) -> AwareDatetime | None:
    """Parse a datetime string from Mem0 into an aware datetime.

    Mem0 stores timestamps as ISO 8601 strings.  Naive datetimes
    are assumed UTC.

    Args:
        raw: ISO 8601 datetime string, or ``None``.

    Returns:
        Aware datetime or ``None`` if input is ``None`` or empty.
    """
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def normalize_relevance_score(score: float | None) -> float | None:
    """Clamp a relevance score to [0.0, 1.0].

    Args:
        score: Raw score from Mem0 (may exceed bounds).

    Returns:
        Clamped score, or ``None`` if input is ``None``.
    """
    if score is None:
        return None
    return max(0.0, min(1.0, score))


def parse_mem0_metadata(
    raw_metadata: dict[str, Any] | None,
) -> tuple[MemoryCategory, MemoryMetadata, AwareDatetime | None]:
    """Deserialize Mem0 metadata dict into domain objects.

    Args:
        raw_metadata: Metadata dict from Mem0 result (may be ``None``).

    Returns:
        Tuple of (category, metadata, expires_at).
    """
    if not raw_metadata:
        return (
            MemoryCategory.WORKING,
            MemoryMetadata(),
            None,
        )

    category_str = raw_metadata.get(f"{_PREFIX}category")
    category = MemoryCategory(category_str) if category_str else MemoryCategory.WORKING

    confidence = raw_metadata.get(f"{_PREFIX}confidence", 1.0)
    source = raw_metadata.get(f"{_PREFIX}source")
    raw_tags = raw_metadata.get(f"{_PREFIX}tags", ())
    tags = tuple(NotBlankStr(t) for t in raw_tags if t and str(t).strip())

    expires_at = parse_mem0_datetime(
        raw_metadata.get(f"{_PREFIX}expires_at"),
    )

    metadata = MemoryMetadata(
        source=source,
        confidence=confidence,
        tags=tags,
    )
    return category, metadata, expires_at


def mem0_result_to_entry(
    raw: dict[str, Any],
    agent_id: str,
) -> MemoryEntry:
    """Convert a single Mem0 result dict to a ``MemoryEntry``.

    Args:
        raw: Single result dict from Mem0 (``search``, ``get``, or
            ``get_all``).
        agent_id: Owning agent identifier.

    Returns:
        Domain ``MemoryEntry``.
    """
    memory_id = NotBlankStr(str(raw["id"]))
    content = NotBlankStr(str(raw.get("memory", raw.get("data", ""))))

    created_at = parse_mem0_datetime(raw.get("created_at"))
    if created_at is None:
        created_at = datetime.now(UTC)
    updated_at = parse_mem0_datetime(raw.get("updated_at"))

    raw_metadata = raw.get("metadata")
    category, metadata, expires_at = parse_mem0_metadata(raw_metadata)

    raw_score = raw.get("score")
    relevance_score = normalize_relevance_score(raw_score)

    return MemoryEntry(
        id=memory_id,
        agent_id=NotBlankStr(agent_id),
        category=category,
        content=content,
        metadata=metadata,
        created_at=created_at,
        updated_at=updated_at,
        expires_at=expires_at,
        relevance_score=relevance_score,
    )


def query_to_mem0_search_args(
    agent_id: str,
    query: MemoryQuery,
) -> dict[str, Any]:
    """Convert a ``MemoryQuery`` to ``Memory.search()`` kwargs.

    Args:
        agent_id: Owning agent identifier.
        query: Retrieval query.

    Returns:
        Dict of kwargs for ``Memory.search()``.

    Raises:
        ValueError: If ``query.text`` is ``None`` (search requires text).
    """
    if query.text is None:
        msg = "search requires query.text to be set"
        raise ValueError(msg)
    return {
        "query": query.text,
        "user_id": agent_id,
        "limit": query.limit,
    }


def query_to_mem0_getall_args(
    agent_id: str,
    query: MemoryQuery,
) -> dict[str, Any]:
    """Convert a ``MemoryQuery`` to ``Memory.get_all()`` kwargs.

    Args:
        agent_id: Owning agent identifier.
        query: Retrieval query.

    Returns:
        Dict of kwargs for ``Memory.get_all()``.
    """
    return {
        "user_id": agent_id,
        "limit": query.limit,
    }


def apply_post_filters(
    entries: tuple[MemoryEntry, ...],
    query: MemoryQuery,
) -> tuple[MemoryEntry, ...]:
    """Apply post-retrieval filters that Mem0 cannot handle natively.

    Filters by category, tags, time range, and minimum relevance.

    Args:
        entries: Raw entries from Mem0.
        query: Original query with filter criteria.

    Returns:
        Filtered entries (order preserved).
    """
    result: list[MemoryEntry] = []
    for entry in entries:
        if query.categories and entry.category not in query.categories:
            continue
        if query.tags and not all(tag in entry.metadata.tags for tag in query.tags):
            continue
        if query.since and entry.created_at < query.since:
            continue
        if query.until and entry.created_at >= query.until:
            continue
        if (
            query.min_relevance > 0.0
            and entry.relevance_score is not None
            and entry.relevance_score < query.min_relevance
        ):
            continue
        result.append(entry)
    return tuple(result)
