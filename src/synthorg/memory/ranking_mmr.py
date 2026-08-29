"""Diversity re-ranking via Maximal Marginal Relevance (MMR).

``apply_diversity_penalty`` re-ranks scored memories to reduce
redundancy, trading relevance against pairwise dissimilarity.  All
functions are functionally pure; logging is the only side effect.
"""

import math
from collections.abc import Callable
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.text_similarity import split_words
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.ranking import ScoredMemory
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_DIVERSITY_RERANK_FAILED,
    MEMORY_DIVERSITY_RERANKED,
)
from synthorg.observability.redaction import safe_error_description

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

    Each pair is compared exactly once, whichever similarity is in use:
    every candidate carries a running maximum that the newly selected
    entry is folded into once per selection.  When ``similarity_fn`` is
    ``None`` (the default), each entry's word bigrams are additionally
    extracted once up front rather than re-tokenising already-selected
    content on every comparison.

    Args:
        scored: Pre-ranked scored memories.
        diversity_lambda: Trade-off between relevance (1.0) and
            diversity (0.0).  Must be in [0.0, 1.0].
        similarity_fn: Optional pairwise text similarity function.
            Defaults to word-bigram Jaccard when ``None``.  Signed
            measures (cosine) are supported.  A supplied function that
            raises, or answers a non-finite value, leaves ``scored`` in
            its existing relevance order.

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


def _best_candidate_position(
    scored: tuple[ScoredMemory, ...],
    remaining_indices: list[int],
    max_sim_by_idx: list[float],
    *,
    diversity_lambda: float,
    penalise: bool,
) -> int:
    """Position in ``remaining_indices`` of the highest-MMR candidate.

    ``penalise`` is ``False`` on the first pass, where nothing has been
    selected and MMR defines the penalty as zero.  The running maxima
    are seeded ``-inf`` so the seed can never win a fold and floor a
    signed similarity, and that sentinel must be kept out of the
    arithmetic: ``(1 - lambda) * -inf`` is ``-inf``, or ``nan`` at
    ``lambda == 1.0``.

    Returns:
        Index into ``remaining_indices``.
    """
    best_position = 0
    best_mmr = -math.inf

    for position, idx in enumerate(remaining_indices):
        relevance = diversity_lambda * scored[idx].combined_score
        penalty = max_sim_by_idx[idx] if penalise else 0.0
        mmr = relevance - (1.0 - diversity_lambda) * penalty
        if mmr > best_mmr:
            best_mmr = mmr
            best_position = position

    return best_position


def _mmr_rerank_bigram(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
) -> tuple[ScoredMemory, ...]:
    """MMR re-ranking with pre-computed bigram sets for each entry.

    Each candidate carries its similarity to the *nearest* entry selected
    so far, and only the entry just selected can raise that maximum, so
    folding it in once per selection derives each pair exactly once and
    holds the pass at the ``O(n**2)`` the design specifies; recomputing
    over the whole selected set each time costs ``sum (n - s) * s``,
    which is cubic. The running maximum is exact rather than
    approximate: ``max`` is associative and idempotent and accumulates
    no floating-point error.

    Returns:
        Tuple of ``ScoredMemory``.
    """
    bigrams_by_idx = [_word_bigrams(s.entry.content) for s in scored]
    remaining_indices = list(range(len(scored)))
    selected_indices: list[int] = []
    max_sim_by_idx = [-math.inf] * len(scored)

    while remaining_indices:
        chosen = remaining_indices.pop(
            _best_candidate_position(
                scored,
                remaining_indices,
                max_sim_by_idx,
                diversity_lambda=diversity_lambda,
                penalise=bool(selected_indices),
            )
        )
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
    # Derived rather than built: ``a | b`` would allocate a third set on
    # every pair, as large as both operands together where they share
    # nothing, just to read its length.
    union = len(bigrams_a) + len(bigrams_b) - intersection
    return intersection / union


def _mmr_rerank_generic(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
    similarity_fn: Callable[[str, str], float],
) -> tuple[ScoredMemory, ...]:
    """MMR re-ranking with a caller-supplied similarity function.

    Diversity re-orders a result that is already filtered and already
    ranked by relevance, so an injected function that misbehaves
    degrades to that order.  Propagating instead reaches the retrieval
    pipeline's own handler, which discards the whole result and hands
    the agent no memory at all.

    Returns:
        Tuple of ``ScoredMemory``.
    """
    try:
        selected_indices = _mmr_selection_order(
            scored,
            diversity_lambda=diversity_lambda,
            similarity_fn=similarity_fn,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEMORY_DIVERSITY_RERANK_FAILED,
            param="similarity_fn",
            input_count=len(scored),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="similarity_fn_failed_falling_back_to_relevance_order",
        )
        return scored

    logger.info(
        MEMORY_DIVERSITY_RERANKED,
        input_count=len(scored),
        diversity_lambda=diversity_lambda,
        similarity="custom",
    )

    return tuple(scored[i] for i in selected_indices)


def _mmr_selection_order(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
    similarity_fn: Callable[[str, str], float],
) -> list[int]:
    """Selection order under MMR, asking ``similarity_fn`` once per pair.

    Carries the same running maximum as the bigram path, which matters more
    here: the injected function is handed raw text and cannot be assumed
    cheap, so each pair is asked for exactly once.

    Returns:
        Indices into ``scored``, in selection order.

    Raises:
        MemoryConfigError: If ``similarity_fn`` answers a non-finite
            value, which orders nothing against anything.
    """
    remaining_indices = list(range(len(scored)))
    selected_indices: list[int] = []
    max_sim_by_idx = [-math.inf] * len(scored)

    while remaining_indices:
        chosen = remaining_indices.pop(
            _best_candidate_position(
                scored,
                remaining_indices,
                max_sim_by_idx,
                diversity_lambda=diversity_lambda,
                penalise=bool(selected_indices),
            )
        )
        selected_indices.append(chosen)
        chosen_content = scored[chosen].entry.content
        for idx in remaining_indices:
            similarity = similarity_fn(scored[idx].entry.content, chosen_content)
            if not math.isfinite(similarity):
                msg = f"similarity_fn must answer a finite float, got {similarity}"
                raise MemoryConfigError(msg)
            max_sim_by_idx[idx] = max(max_sim_by_idx[idx], similarity)

    return selected_indices
