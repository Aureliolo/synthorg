"""Memory ranking -- linear scoring and sorting.

All functions are functionally pure (deterministic given the same
inputs).  Logging calls are the only side effect.

``rank_memories`` scores entries via linear combination of relevance
and recency (single-source).  Reciprocal Rank Fusion lives in
``ranking_rrf`` (multi-source merge) and MMR diversity re-ranking in
``ranking_mmr``; both depend on ``ScoredMemory`` / ``FusionStrategy``
defined here.
"""

import math
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.memory.models import MemoryEntry
from synthorg.observability import get_logger
from synthorg.observability.events.memory import MEMORY_RANKING_COMPLETE

if TYPE_CHECKING:
    from synthorg.memory.retrieval_config import MemoryRetrievalConfig

logger = get_logger(__name__)

_SECONDS_PER_HOUR: Final[float] = 3600.0


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
            or None when the producer leaves it unset.
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
    age_hours = age_seconds / _SECONDS_PER_HOUR
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
