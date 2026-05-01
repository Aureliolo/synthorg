"""Qdrant sparse vector operations for BM25 hybrid search.

Pure functions operating on a ``QdrantClient`` reference.  These
handle the sparse vector lifecycle: field creation, upsert alongside
dense vectors, and sparse-only retrieval.  Qdrant's ``Modifier.IDF``
applies IDF scoring server-side -- only term frequencies are stored.

Async adapter-level helpers (``async_init_sparse_field``,
``async_try_sparse_upsert``, ``async_retrieve_sparse``) wrap the
sync Qdrant functions for use by ``Mem0MemoryBackend``.
"""

import asyncio
import builtins
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import (
    MemoryRetrievalError,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryQuery
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_SYSTEM_ERROR,
    MEMORY_ENTRY_RETRIEVAL_FAILED,
    MEMORY_SPARSE_BATCH_DEGRADED,
    MEMORY_SPARSE_FIELD_ENSURE_FAILED,
    MEMORY_SPARSE_FIELD_ENSURED,
    MEMORY_SPARSE_POINT_FIELD_DEFAULTED,
    MEMORY_SPARSE_SEARCH_COMPLETE,
    MEMORY_SPARSE_SEARCH_FAILED,
    MEMORY_SPARSE_UPSERT_COMPLETE,
    MEMORY_SPARSE_UPSERT_FAILED,
)

if TYPE_CHECKING:
    from synthorg.memory.sparse import BM25Tokenizer, SparseVector

logger = get_logger(__name__)

_DEFAULT_FIELD_NAME = "bm25"
_SYNTHORG_PREFIX = "_synthorg_"


def ensure_sparse_field(
    client: Any,
    collection_name: str,
    field_name: str = _DEFAULT_FIELD_NAME,
) -> None:
    """Add a sparse vector field to an existing Qdrant collection.

    Idempotent -- skips if the field already exists.  Uses
    ``Modifier.IDF`` so Qdrant applies IDF scoring server-side.

    Args:
        client: ``QdrantClient`` instance.
        collection_name: Target Qdrant collection.
        field_name: Name for the sparse vector field.
    """
    from qdrant_client import models  # noqa: PLC0415

    info = client.get_collection(collection_name)
    existing_sparse = info.config.params.sparse_vectors
    if existing_sparse is not None and field_name in existing_sparse:
        logger.debug(
            MEMORY_SPARSE_FIELD_ENSURED,
            collection=collection_name,
            field_name=field_name,
            action="skipped",
        )
        return

    client.update_collection(
        collection_name=collection_name,
        sparse_vectors_config={
            field_name: models.SparseVectorParams(
                modifier=models.Modifier.IDF,
            ),
        },
    )
    logger.info(
        MEMORY_SPARSE_FIELD_ENSURED,
        collection=collection_name,
        field_name=field_name,
        action="created",
    )


def upsert_sparse_vector(
    client: Any,
    collection_name: str,
    point_id: str,
    sparse_vector: SparseVector,
    field_name: str = _DEFAULT_FIELD_NAME,
) -> None:
    """Attach a sparse vector to an existing Qdrant point.

    Skips empty vectors silently.  Uses ``update_vectors`` to add
    the sparse field without replacing the existing dense vector.

    Args:
        client: ``QdrantClient`` instance.
        collection_name: Target Qdrant collection.
        point_id: UUID of the existing point.
        sparse_vector: BM25 term-frequency sparse vector.
        field_name: Name of the sparse vector field.
    """
    if sparse_vector.is_empty:
        return

    from qdrant_client import models  # noqa: PLC0415

    client.update_vectors(
        collection_name=collection_name,
        points=[
            models.PointVectors(
                id=point_id,
                vector={
                    field_name: models.SparseVector(
                        indices=list(sparse_vector.indices),
                        values=list(sparse_vector.values),
                    ),
                },
            ),
        ],
    )
    logger.debug(
        MEMORY_SPARSE_UPSERT_COMPLETE,
        collection=collection_name,
        point_id=point_id,
        num_terms=len(sparse_vector.indices),
    )


def search_sparse(  # noqa: PLR0913
    client: Any,
    collection_name: str,
    query_vector: SparseVector,
    *,
    user_id_filter: str,
    limit: int,
    field_name: str = _DEFAULT_FIELD_NAME,
) -> list[Any]:
    """Query the sparse vector field for BM25 matches.

    Args:
        client: ``QdrantClient`` instance.
        collection_name: Target Qdrant collection.
        query_vector: BM25-encoded query sparse vector.
        user_id_filter: Filter results to this agent's points.
        limit: Maximum results to return.
        field_name: Name of the sparse vector field.

    Returns:
        List of Qdrant ``ScoredPoint`` objects.
    """
    if query_vector.is_empty:
        return []

    from qdrant_client import models  # noqa: PLC0415

    result = client.query_points(
        collection_name=collection_name,
        query=models.SparseVector(
            indices=list(query_vector.indices),
            values=list(query_vector.values),
        ),
        using=field_name,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=user_id_filter),
                ),
            ],
        ),
        limit=limit,
    )

    logger.debug(
        MEMORY_SPARSE_SEARCH_COMPLETE,
        collection=collection_name,
        user_id=user_id_filter,
        num_results=len(result.points),
    )

    return list(result.points)


def scored_points_to_entries(
    points: list[Any],
    agent_id: NotBlankStr,
) -> tuple[MemoryEntry, ...]:
    """Map Qdrant ``ScoredPoint`` objects to ``MemoryEntry`` instances.

    Skips points with malformed payloads rather than failing the
    entire batch.  Scores are clamped to [0.0, 1.0] for consistency
    with the ranking pipeline.

    Args:
        points: Qdrant scored points from sparse search.
        agent_id: Agent identifier for the entries.

    Returns:
        Tuple of memory entries with relevance scores set.
    """
    entries: list[MemoryEntry] = []
    skipped = 0
    for point in points:
        try:
            entry = _point_to_entry(point, agent_id)
            entries.append(entry)
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_SPARSE_SEARCH_FAILED,
                point_id=str(getattr(point, "id", "unknown")),
                reason="system error during point conversion",
                exc_info=True,
            )
            raise
        except Exception:
            skipped += 1
            logger.warning(
                MEMORY_SPARSE_SEARCH_FAILED,
                point_id=str(getattr(point, "id", "unknown")),
                reason="malformed payload",
                exc_info=True,
            )
    if skipped > 0:
        level = logger.error if not entries else logger.warning
        level(
            MEMORY_SPARSE_BATCH_DEGRADED,
            reason="points skipped due to malformed payload",
            total_points=len(points),
            skipped=skipped,
            recovered=len(entries),
        )
    return tuple(entries)


def _extract_metadata(
    metadata_raw: dict[str, Any],
    point_id_str: str,
) -> tuple[Any, float, str | None, tuple[NotBlankStr, ...]]:
    """Extract category, confidence, source, tags from payload metadata.

    Returns:
        Tuple of (category, confidence, source, tags).
    """
    from synthorg.core.enums import MemoryCategory  # noqa: PLC0415

    category_str = metadata_raw.get(f"{_SYNTHORG_PREFIX}category", "episodic")
    try:
        category = MemoryCategory(category_str)
    except ValueError:
        logger.info(
            MEMORY_SPARSE_POINT_FIELD_DEFAULTED,
            point_id=point_id_str,
            field="category",
            original=category_str,
            default="episodic",
        )
        category = MemoryCategory.EPISODIC

    confidence_raw = metadata_raw.get(f"{_SYNTHORG_PREFIX}confidence")
    confidence = confidence_raw if confidence_raw is not None else 1.0
    source_raw = metadata_raw.get(f"{_SYNTHORG_PREFIX}source")
    source = source_raw.strip() or None if isinstance(source_raw, str) else None
    tags_raw = metadata_raw.get(f"{_SYNTHORG_PREFIX}tags")
    if tags_raw is None:
        tags_raw = ()
    elif isinstance(tags_raw, str):
        tags_raw = (tags_raw,)
    elif not isinstance(tags_raw, list | tuple):
        tags_raw = ()
    tags = tuple(NotBlankStr(t) for t in tags_raw if t and str(t).strip())
    return category, confidence, source, tags


def _parse_created_at(
    payload: dict[str, Any],
    point_id_str: str,
) -> datetime:
    """Parse created_at from payload with fallback to epoch sentinel."""
    _epoch = datetime(1970, 1, 1, tzinfo=UTC)
    created_str = payload.get("created_at")
    if not created_str or not isinstance(created_str, str):
        logger.info(
            MEMORY_SPARSE_POINT_FIELD_DEFAULTED,
            point_id=point_id_str,
            field="created_at",
            default="1970-01-01T00:00:00+00:00",
        )
        return _epoch
    try:
        parsed = datetime.fromisoformat(created_str)
    except ValueError, TypeError:
        logger.info(
            MEMORY_SPARSE_POINT_FIELD_DEFAULTED,
            point_id=point_id_str,
            field="created_at",
            original=created_str,
            default="1970-01-01T00:00:00+00:00",
        )
        return _epoch
    # Ensure aware datetime (assume UTC for naive).
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _point_to_entry(point: Any, agent_id: NotBlankStr) -> MemoryEntry:
    """Convert a single Qdrant point to a MemoryEntry.

    Raises:
        ValueError: If the point has no usable content.
    """
    payload = point.payload
    point_id_str = str(getattr(point, "id", "unknown"))
    metadata_raw = payload.get("metadata", {})

    category, confidence, source, tags = _extract_metadata(
        metadata_raw,
        point_id_str,
    )

    data_raw = payload.get("data")
    mem_raw = payload.get("memory", "")
    content = (
        data_raw
        if isinstance(data_raw, str) and data_raw.strip()
        else (mem_raw if isinstance(mem_raw, str) and mem_raw.strip() else "")
    )
    if not content:
        msg = "empty content in Qdrant payload"
        raise ValueError(msg)

    created_at = _parse_created_at(payload, point_id_str)
    score = min(1.0, max(0.0, float(point.score))) if point.score is not None else None

    # Extract expires_at the same way the dense path does.
    from synthorg.memory.backends.mem0.mappers import (  # noqa: PLC0415
        parse_mem0_datetime,
    )

    expires_at = parse_mem0_datetime(
        metadata_raw.get(f"{_SYNTHORG_PREFIX}expires_at"),
    )

    return MemoryEntry(
        id=NotBlankStr(point_id_str),
        agent_id=agent_id,
        category=category,
        content=NotBlankStr(content),
        metadata=MemoryMetadata(
            confidence=confidence,
            source=NotBlankStr(source) if source else None,
            tags=tags,
        ),
        created_at=created_at,
        expires_at=expires_at,
        relevance_score=score,
    )


# ── Async adapter helpers ─────────────────────────────────────────
# These wrap the sync Qdrant functions for Mem0MemoryBackend,
# keeping sparse orchestration out of the large adapter module.


async def async_init_sparse_field(
    qdrant_client: Any,
    collection_name: str,
) -> None:
    """Initialize sparse vector field (async wrapper).

    Args:
        qdrant_client: ``QdrantClient`` instance.
        collection_name: Target Qdrant collection.
    """
    try:
        await asyncio.to_thread(
            ensure_sparse_field,
            qdrant_client,
            collection_name,
        )
    except builtins.MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            MEMORY_SPARSE_FIELD_ENSURE_FAILED,
            backend="qdrant",
            collection=collection_name,
            operation="init_sparse_field",
            exc_info=True,
        )
        raise


async def async_try_sparse_upsert(  # noqa: PLR0913
    encoder: BM25Tokenizer,
    qdrant_client: Any,
    collection_name: str,
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
    content: str,
) -> None:
    """Upsert a BM25 sparse vector (non-fatal on failure).

    Args:
        encoder: BM25 tokenizer for encoding.
        qdrant_client: ``QdrantClient`` instance.
        collection_name: Target Qdrant collection.
        agent_id: Owning agent identifier (for logging).
        memory_id: Memory entry ID.
        content: Text content to encode.
    """
    try:
        sparse_vec = encoder.encode(content)
        await asyncio.to_thread(
            upsert_sparse_vector,
            qdrant_client,
            collection_name,
            str(memory_id),
            sparse_vec,
        )
    except builtins.MemoryError, RecursionError:
        logger.error(
            MEMORY_BACKEND_SYSTEM_ERROR,
            backend="mem0",
            operation="sparse_upsert",
            agent_id=agent_id,
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.warning(
            MEMORY_SPARSE_UPSERT_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            error=safe_error_description(exc),
            error_type=type(exc).__name__,
        )


async def async_retrieve_sparse(
    encoder: BM25Tokenizer,
    qdrant_client: Any,
    collection_name: str,
    agent_id: NotBlankStr,
    query: MemoryQuery,
) -> tuple[MemoryEntry, ...]:
    """Retrieve memories via BM25 sparse search.

    Returns empty when query text is absent or produces an
    empty vector.

    Args:
        encoder: BM25 tokenizer for encoding.
        qdrant_client: ``QdrantClient`` instance.
        collection_name: Target Qdrant collection.
        agent_id: Owning agent identifier.
        query: Retrieval parameters (uses ``query.text``).

    Returns:
        Matching entries ordered by BM25 relevance.

    Raises:
        MemoryRetrievalError: If the sparse search fails.
    """
    if query.text is None:
        return ()
    try:
        query_vec = encoder.encode(query.text)
        if query_vec.is_empty:
            return ()
        raw_points = await asyncio.to_thread(
            search_sparse,
            qdrant_client,
            collection_name,
            query_vec,
            user_id_filter=str(agent_id),
            limit=query.limit,
        )
        from synthorg.memory.backends.mem0.mappers import (  # noqa: PLC0415
            apply_post_filters,
        )

        entries = scored_points_to_entries(raw_points, agent_id)
        return apply_post_filters(entries, query)
    except builtins.MemoryError, RecursionError:
        logger.error(
            MEMORY_BACKEND_SYSTEM_ERROR,
            backend="mem0",
            operation="retrieve_sparse",
            agent_id=agent_id,
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            agent_id=agent_id,
            error=safe_error_description(exc),
            error_type=type(exc).__name__,
            source="sparse",
        )
        msg = f"Failed sparse retrieval: {exc}"
        raise MemoryRetrievalError(msg) from exc
