"""Row marshalling for the SQLite memory vector repository.

Kept beside the repository rather than inside it so the repository stays
within its module-size budget, and so the row-to-entity mapping (the
part most likely to drift when a column is added) is reviewable on its
own.
"""

import json
import struct
from collections.abc import Sequence
from typing import Any

import aiosqlite

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_MODEL_INVALID
from synthorg.persistence._memory_bm25_ranking import (
    rank_lexical as shared_rank_lexical,
)
from synthorg.persistence._shared import coerce_row_timestamp

logger = get_logger(__name__)


def pack_embedding(embedding: tuple[float, ...]) -> bytes:
    """Pack a float vector into the little-endian float32 blob ``vec0`` wants.

    Args:
        embedding: The vector.

    Returns:
        A ``float32`` byte blob.
    """
    return struct.pack(f"{len(embedding)}f", *embedding)


def encode_tags(tags: tuple[NotBlankStr, ...]) -> str:
    """Serialise tags to the JSON array stored in the ``tags`` column.

    Returns:
        A JSON array string.
    """
    return json.dumps(list(tags))


def _decode_tags(raw: object) -> tuple[NotBlankStr, ...]:
    """Parse the ``tags`` column back into a tuple.

    Returns:
        The tags, or an empty tuple when the column is absent or holds a
        non-list.

    Raises:
        QueryError: If the column holds invalid JSON.
    """
    if raw is None:
        return ()
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        msg = f"Corrupt tags JSON in memory row: {safe_error_description(exc)}"
        logger.warning(
            MEMORY_MODEL_INVALID,
            model="MemoryEntry",
            field="tags",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc
    if not isinstance(parsed, list):
        return ()
    return tuple(NotBlankStr(str(item)) for item in parsed if str(item).strip())


def row_to_entry(  # type: ignore[explicit-any]  # aiosqlite.Row is a Mapping[str, Any]
    row: Any,
    *,
    relevance_score: float | None = None,
) -> MemoryEntry:
    """Convert a ``memory_entries`` row into a :class:`MemoryEntry`.

    Args:
        row: The database row.
        relevance_score: Score to attach, when the row came from a
            ranked read.

    Returns:
        The parsed entry.

    Raises:
        QueryError: If the row holds corrupt or unparseable data.
    """
    try:
        source_raw = row["source"]
        return MemoryEntry(
            id=NotBlankStr(str(row["memory_id"])),
            agent_id=NotBlankStr(str(row["agent_id"])),
            namespace=NotBlankStr(str(row["namespace"])),
            category=MemoryCategory(str(row["category"])),
            content=NotBlankStr(str(row["content"])),
            metadata=MemoryMetadata(
                source=NotBlankStr(str(source_raw)) if source_raw is not None else None,
                confidence=float(row["confidence"]),
                tags=_decode_tags(row["tags"]),
            ),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=(
                coerce_row_timestamp(row["updated_at"])
                if row["updated_at"] is not None
                else None
            ),
            expires_at=(
                coerce_row_timestamp(row["expires_at"])
                if row["expires_at"] is not None
                else None
            ),
            relevance_score=relevance_score,
        )
    except QueryError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        msg = (
            f"Failed to parse memory row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            MEMORY_MODEL_INVALID,
            model="MemoryEntry",
            field="(row)",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def rank_lexical(
    postings: Sequence[aiosqlite.Row],
    stats: aiosqlite.Row | None,
    frequency_rows: Sequence[aiosqlite.Row],
    *,
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Score BM25 postings and order the entries they point at.

    Args:
        postings: One row per (memory, matched term).
        stats: Corpus-wide document count and mean length.
        frequency_rows: Document frequency per matched term.
        limit: Maximum entries to return.

    Returns:
        The top ``limit`` entries by score, each carrying its normalised
        score as ``relevance_score``.
    """
    return shared_rank_lexical(
        postings,
        stats,
        frequency_rows,
        limit=limit,
        row_to_entry=row_to_entry,
    )


__all__ = ["encode_tags", "pack_embedding", "rank_lexical", "row_to_entry"]
