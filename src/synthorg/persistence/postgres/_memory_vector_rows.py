"""Row marshalling for the Postgres memory vector repository.

Postgres returns native types where SQLite returns strings: ``tags`` is
already a decoded list from ``JSONB`` and timestamps arrive as aware
``datetime`` objects, so this is deliberately thinner than the SQLite
sibling rather than a copy of it.
"""

from collections.abc import Sequence

from psycopg.rows import DictRow

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_MODEL_INVALID
from synthorg.persistence._memory_bm25_ranking import (
    rank_lexical as shared_rank_lexical,
)
from synthorg.persistence._shared import normalize_utc

logger = get_logger(__name__)


def _decode_tags(raw: object) -> tuple[NotBlankStr, ...]:
    """Coerce the JSONB ``tags`` column into a tuple.

    Returns:
        The tags, empty when the column is absent or not a list.
    """
    if not isinstance(raw, list):
        return ()
    return tuple(NotBlankStr(str(item)) for item in raw if str(item).strip())


def row_to_entry(
    row: DictRow,
    *,
    relevance_score: float | None = None,
) -> MemoryEntry:
    """Convert a ``memory_entries`` row into a :class:`MemoryEntry`.

    Args:
        row: The database row.
        relevance_score: Score to attach when the row came from a ranked
            read.

    Returns:
        The parsed entry.

    Raises:
        QueryError: If the row holds corrupt or unparseable data.
    """
    try:
        source_raw = row["source"]
        updated_at = row["updated_at"]
        expires_at = row["expires_at"]
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
            created_at=normalize_utc(row["created_at"]),
            updated_at=normalize_utc(updated_at) if updated_at is not None else None,
            expires_at=normalize_utc(expires_at) if expires_at is not None else None,
            relevance_score=relevance_score,
        )
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
    postings: Sequence[DictRow],
    stats: DictRow | None,
    frequency_rows: Sequence[DictRow],
    *,
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Score BM25 postings and order the entries they point at.

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


__all__ = ["rank_lexical", "row_to_entry"]
