"""Memory retrieval pipeline configuration.

Frozen Pydantic config for the retrieval pipeline — weights,
thresholds, and strategy selection.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.memory.injection import InjectionPoint, InjectionStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

_WEIGHT_SUM_TOLERANCE = 1e-6


class MemoryRetrievalConfig(BaseModel):
    """Configuration for the memory retrieval and ranking pipeline.

    Attributes:
        strategy: Which injection strategy to use.
        relevance_weight: Weight for backend relevance score (0.0-1.0).
        recency_weight: Weight for recency decay score (0.0-1.0).
        recency_decay_rate: Exponential decay rate per hour.
        personal_boost: Boost applied to personal over shared (0.0-1.0).
        min_relevance: Minimum combined (relevance + recency) score to include.
        max_memories: Maximum candidates to retrieve (1-100).
        include_shared: Whether to query SharedKnowledgeStore.
        default_relevance: Score for entries missing relevance_score.
        injection_point: Message role for context injection.
        non_inferable_only: When True, auto-creates a ``TagBasedMemoryFilter``
            in ``ContextInjectionStrategy`` if no explicit filter is provided.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    strategy: InjectionStrategy = Field(
        default=InjectionStrategy.CONTEXT,
        description="Which injection strategy to use",
    )
    relevance_weight: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Weight for backend relevance score",
    )
    recency_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight for recency decay score",
    )
    recency_decay_rate: float = Field(
        default=0.01,
        ge=0.0,
        description="Exponential decay rate per hour",
    )
    personal_boost: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Boost applied to personal over shared memories",
    )
    min_relevance: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum combined (relevance + recency) score to include",
    )
    max_memories: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum candidates to retrieve",
    )
    include_shared: bool = Field(
        default=True,
        description="Whether to query SharedKnowledgeStore",
    )
    default_relevance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Score for entries missing relevance_score",
    )
    injection_point: InjectionPoint = Field(
        default=InjectionPoint.SYSTEM,
        description="Message role for context injection",
    )
    non_inferable_only: bool = Field(
        default=False,
        description="When True, only inject memories tagged as non-inferable",
    )

    @model_validator(mode="after")
    def _validate_weight_sum(self) -> Self:
        """Ensure relevance_weight + recency_weight == 1.0 (within tolerance)."""
        total = self.relevance_weight + self.recency_weight
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            msg = (
                f"relevance_weight ({self.relevance_weight}) + "
                f"recency_weight ({self.recency_weight}) must equal 1.0, "
                f"got {total}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="relevance_weight+recency_weight",
                value=total,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_supported_strategy(self) -> Self:
        """Reject strategies that are not yet implemented."""
        if self.strategy != InjectionStrategy.CONTEXT:
            msg = (
                f"Strategy {self.strategy.value!r} is not yet implemented; "
                f"only {InjectionStrategy.CONTEXT.value!r} is supported"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="strategy",
                value=self.strategy.value,
                reason=msg,
            )
            raise ValueError(msg)
        return self
