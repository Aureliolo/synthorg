"""Relevance score curation strategy.

Non-LLM curation that scores items by content richness (length as
a proxy), source diversity, and a deterministic SHA-256 hash
tie-breaker, then returns the top K.
"""

import hashlib
from typing import TYPE_CHECKING, Final

from synthorg.hr.training.models import ContentType, TrainingItem
from synthorg.observability import get_logger
from synthorg.observability.events.training import (
    HR_TRAINING_CURATION_COMPLETE,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.seniority import SeniorityLevel

logger = get_logger(__name__)

_DEFAULT_TOP_K: Final[int] = 50

# Scoring weights.
_CONTENT_RICHNESS_WEIGHT: Final[float] = 0.4
_DIVERSITY_WEIGHT: Final[float] = 0.3
# Deterministic SHA-256 tie-breaker weight (not actual recency).
# Named for clarity: the score is a hash-derived stable ordering,
# not a timestamp signal.
_TIEBREAKER_WEIGHT: Final[float] = 0.3


class RelevanceScoreCuration:
    """Non-LLM relevance scoring curation strategy.

    Scores each item by content richness (length-based proxy),
    source diversity, and deterministic hash-based ordering.
    Returns the top K items sorted by score descending.

    Args:
        top_k: Maximum items to return.
    """

    def __init__(self, *, top_k: int = _DEFAULT_TOP_K) -> None:
        self._top_k = top_k

    @property
    def name(self) -> str:
        """Strategy name."""
        return "relevance"

    async def curate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        new_agent_role: NotBlankStr,  # noqa: ARG002
        new_agent_level: SeniorityLevel,  # noqa: ARG002
        content_type: ContentType,
    ) -> tuple[TrainingItem, ...]:
        """Score and rank items, returning top K.

        Args:
            items: Candidate items.
            new_agent_role: Role of new hire.
            new_agent_level: Seniority level.
            content_type: Content type being curated.

        Returns:
            Top K items with updated relevance scores, descending.
        """
        if not items:
            return ()

        # Compute scores.
        scored: list[tuple[float, TrainingItem]] = []
        source_counts: dict[str, int] = {}
        for item in items:
            source_counts[item.source_agent_id] = (
                source_counts.get(item.source_agent_id, 0) + 1
            )

        max_len = max(len(item.content) for item in items)

        for item in items:
            # Content richness: normalized by max content length.
            richness = len(item.content) / max_len if max_len > 0 else 0.0

            # Diversity: penalize over-represented sources.
            source_count = source_counts.get(item.source_agent_id, 1)
            diversity = 1.0 / source_count

            # Deterministic ordering via content hash (avoids
            # non-determinism from iteration order).
            content_hash = hashlib.sha256(
                item.content.encode(),
            ).hexdigest()
            hash_score = int(content_hash[:8], 16) / 0xFFFFFFFF

            score = (
                _CONTENT_RICHNESS_WEIGHT * richness
                + _DIVERSITY_WEIGHT * diversity
                + _TIEBREAKER_WEIGHT * hash_score
            )
            score = min(max(score, 0.0), 1.0)

            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = scored[: self._top_k]

        result = tuple(
            item.model_copy(update={"relevance_score": score}) for score, item in top_k
        )

        logger.debug(
            HR_TRAINING_CURATION_COMPLETE,
            strategy="relevance",
            content_type=content_type.value,
            input_count=len(items),
            output_count=len(result),
        )
        return result
