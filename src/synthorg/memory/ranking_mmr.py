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
    return _bigram_jaccard_sets(_word_bigrams(text_a), _word_bigrams(text_b))


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
        return _mmr_rerank_bigram(
            scored,
            diversity_lambda=diversity_lambda,
        )

    return _mmr_rerank_generic(
        scored,
        diversity_lambda=diversity_lambda,
        similarity_fn=similarity_fn,
    )


def _mmr_rerank_bigram(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
) -> tuple[ScoredMemory, ...]:
    """MMR re-ranking with pre-computed bigram sets for each entry.

    Each candidate carries its similarity to the *nearest* entry selected so
    far. Only the entry just selected can raise that maximum, so folding it
    in once per selection derives each pair exactly once and holds the whole
    re-ranking at the ``O(n**2)`` comparisons the design specifies.
    Recomputing the maximum over the whole selected set on every pass costs
    ``sum (n - s) * s`` instead, which is cubic. The running maximum is exact
    rather than approximate: ``max`` is associative and idempotent and
    accumulates no floating-point error, so it equals the maximum over the
    whole selected set.

    Returns:
        Tuple of ``ScoredMemory``.
    """
    bigrams_by_idx = [_word_bigrams(s.entry.content) for s in scored]
    remaining_indices = list(range(len(scored)))
    selected_indices: list[int] = []
    max_sim_by_idx = [0.0] * len(scored)

    while remaining_indices:
        best_position = 0
        best_mmr = -math.inf

        for position, idx in enumerate(remaining_indices):
            relevance = diversity_lambda * scored[idx].combined_score
            mmr = relevance - (1.0 - diversity_lambda) * max_sim_by_idx[idx]
            if mmr > best_mmr:
                best_mmr = mmr
                best_position = position

        chosen = remaining_indices.pop(best_position)
        selected_indices.append(chosen)
        chosen_bigrams = bigrams_by_idx[chosen]
        for idx in remaining_indices:
            max_sim_by_idx[idx] = max(
                max_sim_by_idx[idx],
                _bigram_jaccard_sets(bigrams_by_idx[idx], chosen_bigrams),
            )

    logger.info(
        MEMORY_DIVERSITY_RERANKED,
        input_count=len(scored),
        diversity_lambda=diversity_lambda,
        similarity="bigram_jaccard",
    )

    return tuple(scored[i] for i in selected_indices)


def _bigram_jaccard_sets(
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
    # Derived rather than built: ``a | b`` would allocate the larger of the
    # two sets on every pair just to read its length.
    union = len(bigrams_a) + len(bigrams_b) - intersection
    return intersection / union


def _mmr_rerank_generic(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
    similarity_fn: Callable[[str, str], float],
) -> tuple[ScoredMemory, ...]:
    """MMR re-ranking with a caller-supplied similarity function.

    Carries the same running maximum as the bigram path, which matters more
    here: the injected function is handed raw text and cannot be assumed
    cheap, so each pair is asked for exactly once.

    Returns:
        Tuple of ``ScoredMemory``.
    """
    remaining_indices = list(range(len(scored)))
    selected_indices: list[int] = []
    max_sim_by_idx = [0.0] * len(scored)

    while remaining_indices:
        best_position = 0
        best_mmr = -math.inf

        for position, idx in enumerate(remaining_indices):
            relevance = diversity_lambda * scored[idx].combined_score
            mmr = relevance - (1.0 - diversity_lambda) * max_sim_by_idx[idx]
            if mmr > best_mmr:
                best_mmr = mmr
                best_position = position

        chosen = remaining_indices.pop(best_position)
        selected_indices.append(chosen)
        chosen_content = scored[chosen].entry.content
        for idx in remaining_indices:
            max_sim_by_idx[idx] = max(
                max_sim_by_idx[idx],
                similarity_fn(scored[idx].entry.content, chosen_content),
            )

    logger.info(
        MEMORY_DIVERSITY_RERANKED,
        input_count=len(scored),
        diversity_lambda=diversity_lambda,
        similarity="custom",
    )

    return tuple(scored[i] for i in selected_indices)
