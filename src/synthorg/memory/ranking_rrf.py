"""Reciprocal Rank Fusion for merging multiple pre-ranked memory lists.

``fuse_ranked_lists`` merges several pre-sorted ranked lists into a
single ranking via Reciprocal Rank Fusion (multi-source hybrid search).
All functions are functionally pure; logging is the only side effect.
"""

from typing import Final

from synthorg.memory.models import MemoryEntry
from synthorg.memory.ranking import FusionStrategy, ScoredMemory
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_RRF_FUSION_COMPLETE,
    MEMORY_RRF_VALIDATION_FAILED,
)

logger = get_logger(__name__)


def _normalize_rrf_scores(
    scores: dict[str, float],
) -> dict[str, float]:
    """Min-max normalize raw RRF scores to [0.0, 1.0].

    Returns:
        Mapping from ``str`` to ``float``.
    """
    min_score = min(scores.values())
    max_score = max(scores.values())
    score_range = max_score - min_score
    return {
        eid: (score - min_score) / score_range if score_range > 0 else 1.0
        for eid, score in scores.items()
    }


def _build_rrf_scored_memories(
    entries: dict[str, MemoryEntry],
    normalized: dict[str, float],
) -> list[ScoredMemory]:
    """Build ScoredMemory objects from RRF-normalized scores.

    Returns:
        List of ``ScoredMemory``.
    """
    scored: list[ScoredMemory] = []
    for eid, entry in entries.items():
        raw_rel = entry.relevance_score if entry.relevance_score is not None else 0.0
        scored.append(
            ScoredMemory(
                entry=entry,
                relevance_score=raw_rel,
                recency_score=0.0,
                combined_score=normalized[eid],
                scoring_strategy=FusionStrategy.RRF,
            )
        )
    return scored


def _accumulate_rrf_scores(
    ranked_lists: tuple[tuple[MemoryEntry, ...], ...],
    k: int,
) -> tuple[dict[str, float], dict[str, MemoryEntry], int]:
    """Iterate ranked lists, accumulate RRF scores with per-list dedup.

    Returns:
        Tuple of (scores, entries, duplicate_count).
    """
    scores: dict[str, float] = {}
    entries: dict[str, MemoryEntry] = {}
    duplicate_count = 0

    for ranked_list in ranked_lists:
        seen_in_list: set[str] = set()
        unique_rank = 0
        for entry in ranked_list:
            if entry.id in seen_in_list:
                duplicate_count += 1
                continue
            seen_in_list.add(entry.id)
            unique_rank += 1
            scores[entry.id] = scores.get(entry.id, 0.0) + 1.0 / (k + unique_rank)
            if entry.id not in entries:
                entries[entry.id] = entry

    return scores, entries, duplicate_count


_DEFAULT_K: Final[int] = 60
_DEFAULT_MAX_RESULTS: Final[int] = 20


def fuse_ranked_lists(
    ranked_lists: tuple[tuple[MemoryEntry, ...], ...],
    *,
    k: int = _DEFAULT_K,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> tuple[ScoredMemory, ...]:
    """Merge multiple pre-ranked lists via Reciprocal Rank Fusion.

    ``RRF_score(doc) = sum(1 / (k + rank_i))`` across all lists
    containing the document.  Scores are min-max normalized to
    [0.0, 1.0].

    For RRF output, only ``combined_score`` is the meaningful
    ranking signal.  ``relevance_score`` preserves the entry's raw
    backend relevance (or 0.0 if absent); ``recency_score`` is 0.0.

    When the same entry ID appears in multiple lists, the first
    ``MemoryEntry`` object encountered is retained.

    Unlike ``rank_memories``, this function does **not** apply a
    ``min_relevance`` threshold -- callers are responsible for
    post-filtering if needed.

    Args:
        ranked_lists: Each inner tuple is a pre-sorted ranked list
            of memory entries (best first).
        k: RRF smoothing constant (default 60, must be >= 1).
            Smaller values amplify rank differences.
        max_results: Maximum entries to return (must be >= 1).

    Returns:
        Sorted tuple of ``ScoredMemory`` by descending RRF score.

    Raises:
        ValueError: If ``k < 1`` or ``max_results < 1``.
    """
    if k < 1:
        msg = f"k must be >= 1, got {k}"
        logger.warning(MEMORY_RRF_VALIDATION_FAILED, param="k", value=k)
        raise ValueError(msg)
    if max_results < 1:
        msg = f"max_results must be >= 1, got {max_results}"
        logger.warning(
            MEMORY_RRF_VALIDATION_FAILED,
            param="max_results",
            value=max_results,
        )
        raise ValueError(msg)

    scores, entries, duplicate_count = _accumulate_rrf_scores(ranked_lists, k)

    if not entries:
        logger.info(
            MEMORY_RRF_FUSION_COMPLETE,
            num_lists=len(ranked_lists),
            unique_entries=0,
            after_truncation=0,
            duplicate_ids_skipped=duplicate_count,
            k=k,
        )
        return ()

    normalized = _normalize_rrf_scores(scores)
    scored_list = _build_rrf_scored_memories(entries, normalized)
    scored_list.sort(key=lambda s: s.combined_score, reverse=True)
    result = tuple(scored_list[:max_results])

    logger.info(
        MEMORY_RRF_FUSION_COMPLETE,
        num_lists=len(ranked_lists),
        unique_entries=len(entries),
        after_truncation=len(result),
        duplicate_ids_skipped=duplicate_count,
        k=k,
    )

    return result
