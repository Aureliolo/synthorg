"""Memory ranking -- scoring and sorting functions.

All functions are functionally pure (deterministic given the same
inputs).  Logging calls are the only side effect.

``rank_memories`` scores entries via linear combination of relevance
and recency (single-source).  ``fuse_ranked_lists`` merges multiple
pre-ranked lists via Reciprocal Rank Fusion (multi-source).
``apply_diversity_penalty`` re-ranks using MMR to reduce redundancy.
"""

import math
from collections.abc import Callable  # noqa: TC003
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.memory.models import MemoryEntry  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_DIVERSITY_RERANK_FAILED,
    MEMORY_DIVERSITY_RERANKED,
    MEMORY_RANKING_COMPLETE,
    MEMORY_RRF_FUSION_COMPLETE,
    MEMORY_RRF_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.memory.retrieval_config import MemoryRetrievalConfig

logger = get_logger(__name__)


class FusionStrategy(StrEnum):
    """Ranking fusion strategy selection.

    Attributes:
        LINEAR: Weighted linear combination of relevance and recency
            (default, for single-source scoring).
        RRF: Reciprocal Rank Fusion for merging multiple ranked lists
            (for multi-source hybrid search).
    """

    LINEAR = "linear"
    RRF = "rrf"


class ScoredMemory(BaseModel):
    """Memory entry with computed ranking scores.

    Produced by either ``rank_memories`` (LINEAR fusion) or
    ``fuse_ranked_lists`` (RRF fusion).  Field semantics depend on
    which producer created the instance:

    - **LINEAR**: ``relevance_score`` is raw backend relevance plus
      ``personal_boost`` (for personal entries), ``recency_score`` is
      the exponential decay based on age, and ``combined_score`` is
      the weighted linear combination of the two.
    - **RRF**: ``relevance_score`` preserves the raw backend relevance
      (or ``0.0`` if absent), ``recency_score`` is always ``0.0``
      (RRF is rank-based, not time-based), and ``combined_score`` is
      the min-max-normalized fusion score.

    Attributes:
        entry: The original memory entry.
        relevance_score: For LINEAR, post-boost relevance; for RRF,
            raw backend relevance (0.0-1.0).
        recency_score: Exponential decay based on age (LINEAR) or
            always 0.0 (RRF).
        combined_score: Final ranking signal (0.0-1.0).  LINEAR
            weighted combination or RRF normalized fusion score.
        is_shared: Whether this came from SharedKnowledgeStore.
        scoring_strategy: Which fusion strategy produced this instance,
            or None when unset (backward compatibility).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry: MemoryEntry = Field(description="The original memory entry")
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description=("LINEAR: post-boost relevance. RRF: raw backend relevance."),
    )
    recency_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Recency decay score (always 0.0 for RRF).",
    )
    combined_score: float = Field(
        ge=0.0,
        le=1.0,
        description=("LINEAR: weighted combination. RRF: normalized fusion score."),
    )
    is_shared: bool = Field(
        default=False,
        description="Whether from SharedKnowledgeStore",
    )
    scoring_strategy: FusionStrategy | None = Field(
        default=None,
        description=(
            "Which fusion strategy produced this instance "
            "(None when unset by the producer)"
        ),
    )


def compute_recency_score(
    created_at: datetime,
    now: datetime,
    decay_rate: float,
) -> float:
    """Compute exponential recency decay score.

    ``exp(-decay_rate * age_hours)``.  Returns 1.0 for zero age,
    decays toward 0.0 over time.  Future timestamps are clamped to
    1.0.

    Args:
        created_at: When the memory was created.
        now: Current timestamp for age calculation.
        decay_rate: Exponential decay rate per hour.

    Returns:
        Recency score between 0.0 and 1.0.
    """
    age_seconds = (now - created_at).total_seconds()
    if age_seconds <= 0:
        return 1.0
    age_hours = age_seconds / 3600.0
    return math.exp(-decay_rate * age_hours)


def compute_combined_score(
    relevance: float,
    recency: float,
    relevance_weight: float,
    recency_weight: float,
) -> float:
    """Weighted linear combination of relevance and recency.

    Args:
        relevance: Relevance score (0.0-1.0).
        recency: Recency score (0.0-1.0).
        relevance_weight: Weight for relevance.
        recency_weight: Weight for recency.

    Returns:
        Combined score clamped to [0.0, 1.0].  When
        ``relevance_weight + recency_weight == 1.0`` and inputs are
        in [0.0, 1.0], the result is naturally bounded; the clamp
        guards against floating-point tolerance in the weight sum.
    """
    return min(1.0, relevance_weight * relevance + recency_weight * recency)


def _score_entry(
    entry: MemoryEntry,
    *,
    config: MemoryRetrievalConfig,
    now: datetime,
    is_shared: bool,
) -> ScoredMemory:
    """Score a single entry using config weights and decay.

    Personal entries receive ``config.personal_boost`` added to their
    relevance (clamped to 1.0).  Shared entries use raw relevance
    without boost.

    Args:
        entry: The memory entry to score.
        config: Retrieval configuration.
        now: Current timestamp for recency.
        is_shared: Whether this is a shared entry.

    Returns:
        Scored memory with computed scores.
    """
    raw_relevance = (
        entry.relevance_score
        if entry.relevance_score is not None
        else config.default_relevance
    )

    relevance = (
        raw_relevance if is_shared else min(raw_relevance + config.personal_boost, 1.0)
    )

    recency = compute_recency_score(
        entry.created_at,
        now,
        config.recency_decay_rate,
    )

    combined = compute_combined_score(
        relevance,
        recency,
        config.relevance_weight,
        config.recency_weight,
    )

    return ScoredMemory(
        entry=entry,
        relevance_score=relevance,
        recency_score=recency,
        combined_score=combined,
        is_shared=is_shared,
        scoring_strategy=FusionStrategy.LINEAR,
    )


def rank_memories(
    entries: tuple[MemoryEntry, ...],
    *,
    config: MemoryRetrievalConfig,
    now: datetime,
    shared_entries: tuple[MemoryEntry, ...] = (),
) -> tuple[ScoredMemory, ...]:
    """Score, merge, sort, filter, and truncate memory entries.

    1. Score personal entries (with ``personal_boost``).
    2. Score shared entries (no boost).
    3. Merge both sets.
    4. Filter by ``min_relevance`` threshold on ``combined_score``.
    5. Sort descending by ``combined_score``.
    6. Truncate to ``max_memories``.

    Args:
        entries: Personal memory entries.
        config: Retrieval pipeline configuration.
        now: Current timestamp for recency calculations.
        shared_entries: Shared memory entries (no personal boost).

    Returns:
        Sorted and filtered tuple of ``ScoredMemory``.
    """
    scored = [
        _score_entry(entry, config=config, now=now, is_shared=False)
        for entry in entries
    ]
    scored.extend(
        _score_entry(entry, config=config, now=now, is_shared=True)
        for entry in shared_entries
    )

    filtered = [s for s in scored if s.combined_score >= config.min_relevance]
    filtered.sort(key=lambda s: s.combined_score, reverse=True)

    result = tuple(filtered[: config.max_memories])

    logger.debug(
        MEMORY_RANKING_COMPLETE,
        total_candidates=len(scored),
        after_filter=len(filtered),
        after_truncation=len(result),
        min_relevance=config.min_relevance,
        max_memories=config.max_memories,
    )

    return result


def _normalize_rrf_scores(
    scores: dict[str, float],
) -> dict[str, float]:
    """Min-max normalize raw RRF scores to [0.0, 1.0]."""
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
    """Build ScoredMemory objects from RRF-normalized scores."""
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


def fuse_ranked_lists(
    ranked_lists: tuple[tuple[MemoryEntry, ...], ...],
    *,
    k: int = 60,
    max_results: int = 20,
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


# ── Diversity re-ranking (MMR) ────────────────────────────────────


_MIN_BIGRAM_WORDS: Final[int] = 2


def _word_bigrams(text: str) -> frozenset[tuple[str, str]]:
    """Extract word-level bigrams from ``text``.

    Args:
        text: Input text.

    Returns:
        Frozen set of consecutive (word_i, word_i+1) pairs (lowercased).
        Empty when the text has fewer than two words.
    """
    words = text.lower().split()
    if len(words) < _MIN_BIGRAM_WORDS:
        return frozenset()
    return frozenset((words[i], words[i + 1]) for i in range(len(words) - 1))


def bigram_jaccard(text_a: str, text_b: str) -> float:
    """Word-bigram Jaccard similarity between two texts.

    Returns 0.0 when either text has fewer than 2 words (no bigrams
    possible).

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    bigrams_a = _word_bigrams(text_a)
    bigrams_b = _word_bigrams(text_b)
    if not bigrams_a or not bigrams_b:
        return 0.0
    intersection = len(bigrams_a & bigrams_b)
    union = len(bigrams_a | bigrams_b)
    return intersection / union


def apply_diversity_penalty(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float = 0.7,
    similarity_fn: Callable[[str, str], float] | None = None,
) -> tuple[ScoredMemory, ...]:
    """Re-rank scored memories using Maximal Marginal Relevance.

    Iteratively selects entries that balance relevance (via
    ``combined_score``) with diversity (via pairwise dissimilarity
    to already-selected entries).

    MMR score: ``lambda * combined_score - (1 - lambda) * max_sim``

    When ``similarity_fn`` is ``None`` (the default), the implementation
    pre-computes each entry's word bigrams once and computes Jaccard
    from the cached sets, avoiding ``O(n**2 * k)`` re-tokenization of
    already-selected content on each iteration.

    Args:
        scored: Pre-ranked scored memories.
        diversity_lambda: Trade-off between relevance (1.0) and
            diversity (0.0).  Must be in [0.0, 1.0].
        similarity_fn: Optional pairwise text similarity function.
            Defaults to bigram Jaccard (with precomputed bigram cache)
            when ``None``.

    Returns:
        Re-ordered tuple of the same length as ``scored``.

    Raises:
        ValueError: If ``diversity_lambda`` is outside [0.0, 1.0].
    """
    if (
        not math.isfinite(diversity_lambda)
        or diversity_lambda < 0.0
        or diversity_lambda > 1.0
    ):
        msg = (
            f"diversity_lambda must be a finite float in [0.0, 1.0], "
            f"got {diversity_lambda}"
        )
        logger.warning(
            MEMORY_DIVERSITY_RERANK_FAILED,
            param="diversity_lambda",
            value=diversity_lambda,
            reason=msg,
        )
        raise ValueError(msg)

    if len(scored) <= 1:
        return scored

    if similarity_fn is None:
        return _mmr_rerank_bigram_cached(
            scored,
            diversity_lambda=diversity_lambda,
        )

    return _mmr_rerank_generic(
        scored,
        diversity_lambda=diversity_lambda,
        similarity_fn=similarity_fn,
    )


def _mmr_rerank_bigram_cached(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
) -> tuple[ScoredMemory, ...]:
    """MMR re-ranking with pre-computed bigram sets for each entry."""
    bigrams_by_idx = [_word_bigrams(s.entry.content) for s in scored]
    remaining_indices = list(range(len(scored)))
    selected_indices: list[int] = []

    while remaining_indices:
        best_position = 0
        best_mmr = -math.inf

        for position, idx in enumerate(remaining_indices):
            relevance = diversity_lambda * scored[idx].combined_score
            if selected_indices:
                max_sim = max(
                    _bigram_jaccard_cached(bigrams_by_idx[idx], bigrams_by_idx[sel])
                    for sel in selected_indices
                )
            else:
                max_sim = 0.0
            mmr = relevance - (1.0 - diversity_lambda) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_position = position

        selected_indices.append(remaining_indices.pop(best_position))

    logger.info(
        MEMORY_DIVERSITY_RERANKED,
        input_count=len(scored),
        diversity_lambda=diversity_lambda,
        similarity="bigram_jaccard_cached",
    )

    return tuple(scored[i] for i in selected_indices)


def _bigram_jaccard_cached(
    bigrams_a: frozenset[tuple[str, str]],
    bigrams_b: frozenset[tuple[str, str]],
) -> float:
    """Jaccard similarity between two pre-computed bigram sets."""
    if not bigrams_a or not bigrams_b:
        return 0.0
    intersection = len(bigrams_a & bigrams_b)
    union = len(bigrams_a | bigrams_b)
    return intersection / union


def _mmr_rerank_generic(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
    similarity_fn: Callable[[str, str], float],
) -> tuple[ScoredMemory, ...]:
    """MMR re-ranking with a caller-supplied similarity function."""
    remaining = list(scored)
    selected: list[ScoredMemory] = []

    while remaining:
        best_idx = 0
        best_mmr = -math.inf

        for i, candidate in enumerate(remaining):
            relevance = diversity_lambda * candidate.combined_score
            if selected:
                max_sim = max(
                    similarity_fn(candidate.entry.content, s.entry.content)
                    for s in selected
                )
            else:
                max_sim = 0.0
            mmr = relevance - (1.0 - diversity_lambda) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i

        selected.append(remaining.pop(best_idx))

    logger.info(
        MEMORY_DIVERSITY_RERANKED,
        input_count=len(scored),
        diversity_lambda=diversity_lambda,
        similarity="custom",
    )

    return tuple(selected)
