"""Bidirectional mapping between SynthOrg domain models and Mem0 dicts.

Stateless mapping functions — no I/O, no persistent side effects.
Each mapper handles one direction of the conversion so the adapter
stays thin.
"""

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import (
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_ENTRY_DELETE_FAILED,
    MEMORY_ENTRY_RETRIEVAL_FAILED,
    MEMORY_ENTRY_STORE_FAILED,
    MEMORY_FILTER_APPLIED,
    MEMORY_MODEL_INVALID,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

logger = get_logger(__name__)

# Metadata prefix avoids collisions with Mem0's own keys.
_PREFIX = "_synthorg_"

# Metadata key to track who published a shared memory.
# Public because the adapter module needs it for ownership tracking.
PUBLISHER_KEY: str = f"{_PREFIX}publisher"

# Reserved user_id for the shared knowledge namespace.
# All shared memories are stored under this Mem0 ``user_id`` so they
# are isolated from per-agent memories and can be queried centrally.
SHARED_NAMESPACE: str = "__synthorg_shared__"


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


def normalize_relevance_score(score: Any) -> float | None:
    """Coerce and clamp a relevance score to [0.0, 1.0].

    Args:
        score: Raw score from Mem0 (may be ``None``, numeric,
            or a string representation of a number).

    Returns:
        Clamped score, or ``None`` if input is ``None`` or
        cannot be converted to a float.
    """
    if score is None:
        return None
    try:
        numeric = float(score)
    except ValueError, TypeError:
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="score",
            raw_value=score,
            reason="non-numeric relevance score, returning None",
        )
        return None
    if not math.isfinite(numeric):
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="score",
            raw_value=score,
            reason="non-finite relevance score, returning None",
        )
        return None
    return max(0.0, min(1.0, numeric))


def _coerce_confidence(raw_metadata: dict[str, Any]) -> float:
    """Extract and clamp confidence from Mem0 metadata.

    Returns a float in [0.0, 1.0].  Defaults to 1.0 when the key is
    absent (newly stored entries always write it), or 0.5 when the
    value is present but non-numeric (corrupt data gets a conservative
    mid-range default rather than maximum confidence).
    """
    raw = raw_metadata.get(f"{_PREFIX}confidence", 1.0)
    try:
        value = float(raw)
    except ValueError, TypeError:
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="confidence",
            raw_value=raw,
            reason="non-numeric confidence, defaulting to 0.5",
        )
        return 0.5
    if not math.isfinite(value):
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="confidence",
            raw_value=raw,
            reason="non-finite confidence, defaulting to 0.5",
        )
        return 0.5
    return max(0.0, min(1.0, value))


def _coerce_source(raw_metadata: dict[str, Any]) -> str | None:
    """Extract and sanitize the source field from Mem0 metadata.

    Returns ``None`` if the value is missing, non-string, or blank.
    """
    raw = raw_metadata.get(f"{_PREFIX}source")
    if raw is None:
        return None
    coerced = str(raw).strip()
    if not coerced:
        logger.debug(
            MEMORY_MODEL_INVALID,
            field="source",
            raw_value=raw,
            reason="blank source after coercion, returning None",
        )
        return None
    return coerced


def _normalize_tags(
    raw_metadata: dict[str, Any],
) -> tuple[NotBlankStr, ...]:
    """Extract and normalize tags from Mem0 metadata.

    Handles string, list, tuple, and unexpected types gracefully.
    """
    raw_tags = raw_metadata.get(f"{_PREFIX}tags", ())
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    elif not isinstance(raw_tags, (list, tuple)):
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="tags",
            raw_value=type(raw_tags).__name__,
            reason="unexpected tags type, ignoring",
        )
        raw_tags = ()
    valid: list[NotBlankStr] = []
    for t in raw_tags:
        stripped = str(t).strip() if t else ""
        if stripped:
            valid.append(NotBlankStr(stripped))
        else:
            logger.debug(
                MEMORY_MODEL_INVALID,
                field="tags",
                raw_value=t,
                reason="blank or falsy tag dropped",
            )
    return tuple(valid)


def parse_mem0_metadata(
    raw_metadata: dict[str, Any] | None,
) -> tuple[MemoryCategory, MemoryMetadata, AwareDatetime | None]:
    """Deserialize Mem0 metadata dict into domain objects.

    Args:
        raw_metadata: Metadata dict from Mem0 result (may be ``None``).

    Returns:
        Tuple of (category, metadata, expires_at).
    """
    if not raw_metadata or not isinstance(raw_metadata, dict):
        log_kwargs = {
            "field": "metadata",
            "raw_value": type(raw_metadata).__name__ if raw_metadata else None,
            "reason": "missing or non-dict metadata, using defaults",
        }
        if raw_metadata is not None:
            logger.warning(MEMORY_MODEL_INVALID, **log_kwargs)
        else:
            logger.debug(MEMORY_MODEL_INVALID, **log_kwargs)
        return (
            MemoryCategory.WORKING,
            MemoryMetadata(),
            None,
        )

    # Delegate to extract_category for consistent fallback logic.
    category = extract_category({"metadata": raw_metadata})

    confidence = _coerce_confidence(raw_metadata)
    source = _coerce_source(raw_metadata)
    tags = _normalize_tags(raw_metadata)
    expires_at = parse_mem0_datetime(
        raw_metadata.get(f"{_PREFIX}expires_at"),
    )

    metadata = MemoryMetadata(
        source=source,
        confidence=confidence,
        tags=tags,
    )
    return category, metadata, expires_at


def _resolve_created_at(
    raw: dict[str, Any],
    *,
    updated_at: AwareDatetime | None,
    expires_at: AwareDatetime | None,
) -> AwareDatetime:
    """Pick the best fallback when ``created_at`` is missing.

    Uses the earliest available candidate to avoid violating
    ``MemoryEntry`` invariants (``updated_at >= created_at``,
    ``expires_at >= created_at``).
    """
    candidates: list[datetime] = []
    if updated_at is not None:
        candidates.append(updated_at)
    if expires_at is not None:
        candidates.append(expires_at)
    if candidates:
        fallback = min(candidates)
        sources = []
        if updated_at is not None:
            sources.append("updated_at")
        if expires_at is not None:
            sources.append("expires_at")
        fallback_source = (
            f"min({', '.join(sources)})" if len(sources) > 1 else sources[0]
        )
    else:
        fallback = datetime.now(UTC)
        fallback_source = "now()"
    logger.warning(
        MEMORY_MODEL_INVALID,
        field="created_at",
        memory_id=str(raw.get("id", "?")),
        reason=f"missing or unparseable created_at, defaulting to {fallback_source}",
    )
    return fallback


def mem0_result_to_entry(
    raw: dict[str, Any],
    agent_id: NotBlankStr,
) -> MemoryEntry:
    """Convert a single Mem0 result dict to a ``MemoryEntry``.

    Args:
        raw: Single result dict from Mem0 (``search``, ``get``, or
            ``get_all``).
        agent_id: Owning agent identifier (must be ``NotBlankStr``).

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
    updated_at = parse_mem0_datetime(raw.get("updated_at"))

    raw_metadata = raw.get("metadata")
    category, metadata, expires_at = parse_mem0_metadata(raw_metadata)

    if created_at is None:
        created_at = _resolve_created_at(
            raw,
            updated_at=updated_at,
            expires_at=expires_at,
        )

    raw_score = raw.get("score")
    relevance_score = normalize_relevance_score(raw_score)

    return MemoryEntry(
        id=memory_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=metadata,
        created_at=created_at,
        updated_at=updated_at,
        expires_at=expires_at,
        relevance_score=relevance_score,
    )


def query_to_mem0_search_args(
    agent_id: NotBlankStr,
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
        "user_id": str(agent_id),
        "limit": query.limit,
    }


def query_to_mem0_getall_args(
    agent_id: NotBlankStr,
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
        "user_id": str(agent_id),
        "limit": query.limit,
    }


def apply_post_filters(
    entries: tuple[MemoryEntry, ...],
    query: MemoryQuery,
) -> tuple[MemoryEntry, ...]:
    """Apply post-retrieval filters that Mem0 cannot handle natively.

    Filters expired entries, then applies category, tags, time range,
    and minimum relevance filters.  Entries with
    ``relevance_score=None`` (e.g. from ``get_all``) are never
    excluded by ``min_relevance`` — the filter only applies when a
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
    result: list[MemoryEntry] = []
    for entry in entries:
        if entry.expires_at is not None and entry.expires_at <= now:
            continue
        if query.categories and entry.category not in query.categories:
            continue
        if query.tags and not all(tag in entry.metadata.tags for tag in query.tags):
            continue
        if query.since is not None and entry.created_at < query.since:
            continue
        if query.until is not None and entry.created_at >= query.until:
            continue
        if (
            query.min_relevance > 0.0
            and entry.relevance_score is not None
            and entry.relevance_score < query.min_relevance
        ):
            continue
        result.append(entry)
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


# ── Adapter helpers ──────────────────────────────────────────────────


def validate_add_result(result: Any, *, context: str) -> NotBlankStr:
    """Extract and validate the memory ID from a Mem0 ``add`` result.

    Args:
        result: Raw result from ``Memory.add()`` (expected dict).
        context: Human-readable context for error messages
            (e.g. ``"store"`` or ``"shared publish"``).

    Returns:
        The backend-assigned memory ID.

    Raises:
        MemoryStoreError: If the result is missing or malformed.
    """
    if not isinstance(result, dict):
        msg = (
            f"Mem0 add returned unexpected type for {context}: {type(result).__name__}"
        )
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    results_list = result.get("results")
    if not isinstance(results_list, list) or not results_list:
        msg = f"Mem0 add returned no results for {context}"
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    first = results_list[0]
    if not isinstance(first, dict):
        msg = (
            f"Mem0 add result item is not a dict for {context}: {type(first).__name__}"
        )
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
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
    if not metadata or not isinstance(metadata, dict):
        logger.debug(
            MEMORY_MODEL_INVALID,
            field="category",
            raw_value=type(metadata).__name__ if metadata else None,
            reason="missing or non-dict metadata, defaulting to WORKING",
        )
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
    logger.debug(
        MEMORY_MODEL_INVALID,
        field="category",
        reason="category key absent from metadata, defaulting to WORKING",
    )
    return MemoryCategory.WORKING


def validate_mem0_result(
    raw_result: Any,
    *,
    context: str,
) -> list[dict[str, Any]]:
    """Validate and extract the results list from a Mem0 response.

    Args:
        raw_result: Raw return value from a Mem0 SDK call.
        context: Human-readable context for error messages.

    Returns:
        The ``"results"`` list from the response.

    Raises:
        MemoryRetrievalError: If the response is not a dict or
            ``"results"`` is not a list.
    """
    if not isinstance(raw_result, dict):
        msg = (
            f"Unexpected Mem0 response type for {context}: "
            f"{type(raw_result).__name__}, expected dict"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    if "results" not in raw_result:
        msg = (
            f"Mem0 response missing 'results' key for {context}: "
            f"keys={list(raw_result.keys())}"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    raw_list = raw_result["results"]
    if not isinstance(raw_list, list):
        msg = (
            f"Unexpected Mem0 results type for {context}: "
            f"{type(raw_list).__name__}, expected list"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    return raw_list


def resolve_publisher(item: dict[str, Any]) -> str:
    """Extract publisher from a shared memory, defaulting to namespace.

    Logs at DEBUG when publisher metadata is missing.
    """
    publisher = extract_publisher(item)
    if publisher is None:
        logger.debug(
            MEMORY_MODEL_INVALID,
            memory_id=item.get("id", "?"),
            reason="no publisher metadata — attributing to shared namespace",
        )
        return SHARED_NAMESPACE
    return publisher


def extract_publisher(raw: dict[str, Any]) -> NotBlankStr | None:
    """Extract the publisher agent ID from a shared memory dict.

    Returns ``None`` if the publisher key is missing, non-dict
    metadata, or the value is blank after coercion and stripping.
    """
    metadata = raw.get("metadata", {})
    if not metadata or not isinstance(metadata, dict):
        return None
    value = metadata.get(PUBLISHER_KEY)
    if value is None:
        return None
    coerced = str(value).strip()
    return NotBlankStr(coerced) if coerced else None


def check_delete_ownership(
    existing: dict[str, Any],
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
) -> None:
    """Verify the caller owns this private memory entry.

    Raises:
        MemoryStoreError: If ownership cannot be verified
            (missing user_id, shared namespace entry, or
            ownership mismatch).
    """
    owner = existing.get("user_id")
    if owner is None:
        msg = (
            f"Memory {memory_id} has no user_id — ownership "
            f"unverifiable, refusing deletion"
        )
        logger.warning(
            MEMORY_ENTRY_DELETE_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="unverifiable_ownership",
        )
        raise MemoryStoreError(msg)
    if str(owner) == SHARED_NAMESPACE:
        msg = (
            f"Memory {memory_id} belongs to the shared namespace — "
            f"use retract() to remove shared entries"
        )
        logger.warning(
            MEMORY_ENTRY_DELETE_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="shared namespace entry",
        )
        raise MemoryStoreError(msg)
    if str(owner) != str(agent_id):
        msg = f"Agent {agent_id} cannot delete memory {memory_id} owned by {owner}"
        logger.warning(
            MEMORY_ENTRY_DELETE_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="ownership mismatch",
            actual_owner=str(owner),
        )
        raise MemoryStoreError(msg)
