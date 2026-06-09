"""Diversity re-ranking via Maximal Marginal Relevance (MMR).

``apply_diversity_penalty`` re-ranks scored memories to reduce
redundancy, trading relevance against pairwise dissimilarity.  All
functions are functionally pure; logging is the only side effect.
"""

import math
from collections.abc import Callable
from typing import Final

from synthorg.core.text_similarity import split_words
from synthorg.memory.ranking import ScoredMemory
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_DIVERSITY_RERANK_FAILED,
    MEMORY_DIVERSITY_RERANKED,
)

logger = get_logger(__name__)


_MIN_BIGRAM_WORDS: Final[int] = 2


def _word_bigrams(text: str) -> frozenset[tuple[str, str]]:
    """Extract word-level bigrams from ``text``.

    Args:
        text: Input text.

    Returns:
        Frozen set of consecutive (word_i, word_i+1) pairs (lowercased).
        Empty when the text has fewer than two words.
    """
    words = split_words(text)
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


_DEFAULT_DIVERSITY_LAMBDA: Final[float] = 0.7


def apply_diversity_penalty(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float = _DEFAULT_DIVERSITY_LAMBDA,
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
    """MMR re-ranking with pre-computed bigram sets for each entry.

    Returns:
        Tuple of ``ScoredMemory``.
    """
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
    """Jaccard similarity between two pre-computed bigram sets.

    Returns:
        Result of type ``float``.
    """
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
    """MMR re-ranking with a caller-supplied similarity function.

    Returns:
        Tuple of ``ScoredMemory``.
    """
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
