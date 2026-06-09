"""Bidirectional mapping between SynthOrg domain models and Mem0 dicts.

Stateless mapping functions -- no I/O, no persistent side effects.
Each mapper handles one direction of the conversion so the adapter
stays thin.
"""

import math
from collections.abc import Mapping
from datetime import UTC, datetime

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.mem0.mappers_shared import _PREFIX
from synthorg.memory.errors import (
    MemoryRetrievalError,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_MODEL_INVALID,
)

logger = get_logger(__name__)


def build_mem0_metadata(request: MemoryStoreRequest) -> dict[str, object]:
    """Serialize a store request's metadata into Mem0-compatible dict.

    Args:
        request: Memory store request with category and metadata.

    Returns:
        Dict of prefixed metadata fields for Mem0.
    """
    meta: dict[str, object] = {
        f"{_PREFIX}category": request.category.value,
        f"{_PREFIX}namespace": request.namespace,
        f"{_PREFIX}confidence": request.metadata.confidence,
    }
    if request.metadata.source is not None:
        meta[f"{_PREFIX}source"] = request.metadata.source
    if request.metadata.tags:
        meta[f"{_PREFIX}tags"] = list(request.metadata.tags)
    if request.expires_at is not None:
        meta[f"{_PREFIX}expires_at"] = request.expires_at.isoformat()
    return meta


def parse_mem0_datetime(raw: object) -> datetime | None:
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
    if not isinstance(raw, str):
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="datetime",
            raw_value=raw,
            reason="malformed ISO 8601 datetime, returning None",
        )
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
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


def normalize_relevance_score(score: object) -> float | None:
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
    if not isinstance(score, (int, float, str)):
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="score",
            raw_value=score,
            reason="non-numeric relevance score, returning None",
        )
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


def coerce_confidence(raw_metadata: Mapping[str, object]) -> float:
    """Extract and clamp confidence from Mem0 metadata.

    Returns a float in [0.0, 1.0].  Defaults to 1.0 when the key is
    absent (newly stored entries always write it), or 0.5 when the
    value is present but non-numeric (corrupt data gets a conservative
    mid-range default rather than maximum confidence).

    Returns:
        Result of type ``float``.
    """
    raw = raw_metadata.get(f"{_PREFIX}confidence", 1.0)
    if not isinstance(raw, (int, float, str)):
        logger.warning(
            MEMORY_MODEL_INVALID,
            field="confidence",
            raw_value=raw,
            reason="non-numeric confidence, defaulting to 0.5",
        )
        return 0.5
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


def _coerce_source(raw_metadata: Mapping[str, object]) -> str | None:
    """Extract and sanitize the source field from Mem0 metadata.

    Returns ``None`` if the value is missing, non-string, or blank.

    Returns:
        The resulting ``str``, or ``None`` when unavailable.
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
    raw_metadata: Mapping[str, object],
) -> tuple[NotBlankStr, ...]:
    """Extract and normalize tags from Mem0 metadata.

    Handles string, list, tuple, and unexpected types gracefully.

    Returns:
        Tuple of ``NotBlankStr``.
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
    raw_metadata: object,
) -> tuple[MemoryCategory, MemoryMetadata, datetime | None]:
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

    confidence = coerce_confidence(raw_metadata)
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
    raw: Mapping[str, object],
    *,
    updated_at: datetime | None,
    expires_at: datetime | None,
) -> datetime:
    """Pick the best fallback when ``created_at`` is missing.

    Uses the earliest available candidate to avoid violating
    ``MemoryEntry`` invariants (``updated_at >= created_at``,
    ``expires_at >= created_at``).

    Returns:
        Result of type ``datetime``.
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


def _extract_namespace(
    raw_metadata: object,
) -> NotBlankStr:
    """Extract the storage namespace from Mem0 metadata.

    Returns ``"default"`` when the key is absent (backward compat
    with entries stored before the namespace field was added).

    Returns:
        Result of type ``NotBlankStr``.
    """
    if not raw_metadata or not isinstance(raw_metadata, dict):
        return NotBlankStr("default")
    raw = raw_metadata.get(f"{_PREFIX}namespace")
    if raw is None:
        return NotBlankStr("default")
    coerced = str(raw).strip()
    return NotBlankStr(coerced) if coerced else NotBlankStr("default")


def mem0_result_to_entry(
    raw: Mapping[str, object],
    agent_id: NotBlankStr,
) -> MemoryEntry:
    """Convert a single Mem0 result dict to a ``MemoryEntry``.

    Args:
        raw: Single result dict from Mem0 (``search``, ``get``, or
            ``get_all``).
        agent_id: Owning agent identifier (must be ``NotBlankStr``).

    Returns:
        Domain ``MemoryEntry``.

    Raises:
        MemoryRetrievalError: If the related operation fails.
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
    namespace = _extract_namespace(raw_metadata)

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
        namespace=namespace,
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
) -> dict[str, object]:
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
    # Mem0 v2 moved entity IDs from top-level kwargs to the filters dict
    # and renamed ``limit`` to ``top_k``. ``threshold=0.0`` disables the
    # v2 default 0.1 similarity floor so post-filtering remains the sole
    # relevance gate.
    return {
        "query": query.text,
        "filters": {"user_id": str(agent_id)},
        "top_k": query.limit,
        "threshold": 0.0,
    }


def query_to_mem0_getall_args(
    agent_id: NotBlankStr,
    query: MemoryQuery,
) -> dict[str, object]:
    """Convert a ``MemoryQuery`` to ``Memory.get_all()`` kwargs.

    Args:
        agent_id: Owning agent identifier.
        query: Retrieval query.

    Returns:
        Dict of kwargs for ``Memory.get_all()``.
    """
    return {
        "filters": {"user_id": str(agent_id)},
        "top_k": query.limit,
    }


# ── Adapter helpers ──────────────────────────────────────────────────


def extract_category(raw: Mapping[str, object]) -> MemoryCategory:
    """Extract the memory category from a Mem0 result dict.

    Returns ``MemoryCategory.WORKING`` if the category is missing
    or unrecognised.

    Returns:
        Result of type ``MemoryCategory``.
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
