# module-kind: code
"""Backend-agnostic BM25 ranking for the memory vector repositories.

Both repositories claim to rank identically and differ only in how rows
are fetched. Keeping one copy of the scoring, grouping and tie-break
makes that claim structural rather than something copy discipline has to
maintain: a change to the ordering rule cannot now reach one backend and
miss the other.

Rows arrive as an engine-specific mapping (``aiosqlite.Row`` or psycopg's
``DictRow``). The two share no base class, so the row type stays the
engine's own; every read here narrows immediately through
``int()``/``float()``/``str()``, and the caller supplies the marshaller
that turns a row into an entry.
"""

from collections.abc import Sequence
from typing import Any, Protocol

from synthorg.core.types import NotBlankStr
from synthorg.memory.bm25 import normalise_scores, score_document
from synthorg.memory.models import MemoryEntry


class RowToEntry(Protocol):
    """The caller's backend-specific row marshaller."""

    def __call__(  # type: ignore[explicit-any]  # engine-specific row mapping
        self,
        row: Any,
        *,
        relevance_score: float | None = None,
    ) -> MemoryEntry:
        """Convert one row into an entry carrying *relevance_score*.

        Returns:
            The parsed entry.
        """
        ...


def rank_lexical(  # type: ignore[explicit-any]  # engine-specific row mappings
    postings: Sequence[Any],
    stats: Any | None,
    frequency_rows: Sequence[Any],
    *,
    limit: int,
    row_to_entry: RowToEntry,
) -> tuple[MemoryEntry, ...]:
    """Score BM25 postings and order the entries they point at.

    Args:
        postings: One row per (memory, matched term).
        stats: Corpus-wide document count and mean length.
        frequency_rows: Document frequency per matched term.
        limit: Maximum entries to return.
        row_to_entry: The caller's backend-specific row marshaller.

    Returns:
        The top ``limit`` entries by score, each carrying its normalised
        score as ``relevance_score``. Ties break on ``memory_id`` so the
        order is reproducible across runs and across backends.
    """
    doc_count = int(stats["doc_count"]) if stats is not None else 0
    avg_length = float(stats["avg_length"]) if stats is not None else 0.0
    doc_frequencies = {
        NotBlankStr(str(r["term"])): int(r["doc_frequency"]) for r in frequency_rows
    }
    grouped: dict[str, list[Any]] = {}  # type: ignore[explicit-any]  # engine row
    for row in postings:
        grouped.setdefault(str(row["memory_id"]), []).append(row)

    scored: list[tuple[float, Any]] = []  # type: ignore[explicit-any]  # engine row
    for rows in grouped.values():
        head = rows[0]
        score = score_document(
            matched=tuple(
                (NotBlankStr(str(r["term"])), int(r["term_frequency"])) for r in rows
            ),
            doc_length=int(head["token_count"]),
            doc_count=doc_count,
            doc_frequencies=doc_frequencies,
            avg_length=avg_length,
        )
        scored.append((score, head))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1]["memory_id"])))
    top = scored[:limit]
    normalised = normalise_scores(tuple(score for score, _ in top))
    return tuple(
        row_to_entry(row, relevance_score=norm)
        for (_, row), norm in zip(top, normalised, strict=True)
    )


__all__ = ["rank_lexical"]
