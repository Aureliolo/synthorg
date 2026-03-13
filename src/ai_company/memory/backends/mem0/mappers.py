"""Bidirectional mapping between SynthOrg domain models and Mem0 dicts.

Stateless mapping functions — no I/O, no persistent side effects.
Each mapper handles one direction of the conversion so the adapter
stays thin.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ai_company.core.enums import MemoryCategory
from ai_company.core.types import NotBlankStr
from ai_company.memory.errors import MemoryRetrievalError, MemoryStoreError
from ai_company.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from ai_company.observability import get_logger
from ai_company.observability.events.memory import (
    MEMORY_ENTRY_STORE_FAILED,
    MEMORY_MODEL_INVALID,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

logger = get_logger(__name__)

# Metadata prefix avoids collisions with Mem0's own keys.
_PREFIX = "_synthorg_"

# Metadata key to track who published a shared memory.
_PUBLISHER_KEY: str = "_synthorg_publisher"


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
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError, TypeError:
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="datetime",
            raw_value=raw,
            reason="malformed ISO 8601 datetime, returning None",
        )
        return None
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
    if category_str:
        try:
            category = MemoryCategory(category_str)
        except ValueError:
            logger.warning(
                MEMORY_MODEL_INVALID,
                field="category",
                raw_value=category_str,
                reason="unrecognized category, defaulting to WORKING",
            )
            category = MemoryCategory.WORKING
    else:
        category = MemoryCategory.WORKING

    raw_confidence = raw_metadata.get(f"{_PREFIX}confidence", 1.0)
    try:
        confidence = float(raw_confidence)
    except ValueError, TypeError:
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="confidence",
            raw_value=raw_confidence,
            reason="non-numeric confidence, defaulting to 1.0",
        )
        confidence = 1.0
    source = raw_metadata.get(f"{_PREFIX}source")
    raw_tags = raw_metadata.get(f"{_PREFIX}tags", ())
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    elif not isinstance(raw_tags, (list, tuple)):
        raw_tags = ()
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
    raw_id = raw.get("id")
    if raw_id is None or not str(raw_id).strip():
        msg = f"Mem0 result has missing or blank 'id': keys={list(raw.keys())}"
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="id",
            raw_value=raw_id,
            reason=msg,
        )
        raise MemoryRetrievalError(msg)
    memory_id = NotBlankStr(str(raw_id))

    raw_content = raw.get("memory") or raw.get("data")
    if not raw_content or not str(raw_content).strip():
        msg = f"Mem0 result {raw.get('id', '?')} has empty content"
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="content",
            raw_value=raw_content,
            reason=msg,
        )
        raise MemoryRetrievalError(msg)
    content = NotBlankStr(str(raw_content))

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
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="query.text",
            raw_value=None,
            reason=msg,
        )
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


# ── Adapter helpers (moved here to keep adapter.py under 800 lines) ──


def validate_add_result(result: dict[str, Any], *, context: str) -> NotBlankStr:
    """Extract and validate the memory ID from a Mem0 ``add`` result.

    Args:
        result: Raw result dict from ``Memory.add()``.
        context: Human-readable context for error messages
            (e.g. ``"store"`` or ``"shared publish"``).

    Returns:
        The backend-assigned memory ID.

    Raises:
        MemoryStoreError: If the result is missing or malformed.
    """
    results_list = result.get("results")
    if not isinstance(results_list, list) or not results_list:
        msg = f"Mem0 add returned no results for {context}"
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    first = results_list[0]
    raw_id = first.get("id")
    if raw_id is None or not str(raw_id).strip():
        msg = (
            f"Mem0 add result has missing or blank 'id' for {context}: "
            f"keys={list(first.keys())}"
        )
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    return NotBlankStr(str(raw_id))


def extract_category(raw: dict[str, Any]) -> MemoryCategory:
    """Extract the memory category from a Mem0 result dict.

    Returns ``MemoryCategory.WORKING`` if the category is missing
    or unrecognised.
    """
    metadata = raw.get("metadata", {})
    if not metadata:
        return MemoryCategory.WORKING
    cat_str = metadata.get(f"{_PREFIX}category")
    if cat_str:
        try:
            return MemoryCategory(cat_str)
        except ValueError:
            logger.warning(
                MEMORY_MODEL_INVALID,
                field="category",
                raw_value=cat_str,
                reason="unrecognized category in extract_category, "
                "defaulting to WORKING",
            )
            return MemoryCategory.WORKING
    return MemoryCategory.WORKING


def extract_publisher(raw: dict[str, Any]) -> str | None:
    """Extract the publisher agent ID from a shared memory dict."""
    metadata = raw.get("metadata", {})
    if not metadata:
        return None
    value: str | None = metadata.get(_PUBLISHER_KEY)
    return value
