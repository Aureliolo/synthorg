"""Memory retrieval pipeline configuration.

Frozen Pydantic config for the retrieval pipeline -- weights,
thresholds, strategy selection, hierarchical retriever, and
query-specific re-ranking.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.memory.injection import InjectionPoint, InjectionStrategy
from synthorg.memory.ranking import FusionStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

_WEIGHT_SUM_TOLERANCE = 1e-6
_DEFAULT_RRF_K = 60
_DEFAULT_DIVERSITY_LAMBDA = 0.7
_DEFAULT_CANDIDATE_POOL_MULTIPLIER = 3
_DEFAULT_MAX_WORKERS_PER_QUERY = 2
_DEFAULT_RERANK_CACHE_TTL_SECONDS = 3600
_DEFAULT_MAX_RETRY_COUNT = 2


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
        fusion_strategy: Ranking fusion strategy -- LINEAR for single-source
            relevance+recency, RRF for multi-source ranked list merging.
        rrf_k: RRF smoothing constant (1-1000, only used with RRF strategy).
        diversity_penalty_enabled: When True, apply MMR-style diversity
            penalty to re-rank retrieval results, reducing redundancy.
            Only consumed by ``ContextInjectionStrategy``; a validator
            raises if combined with another strategy.  Defaults to
            False.
        diversity_lambda: MMR trade-off parameter (0.0-1.0).  ``1.0``
            means pure relevance (no diversity), ``0.0`` means maximum
            diversity.  Defaults to 0.7.  Only consulted when
            ``diversity_penalty_enabled`` is True.
        query_reformulation_enabled: When True, enables the
            Search-and-Ask iterative query-reformulation loop in the
            TOOL_BASED strategy.  Requires ``ToolBasedInjectionStrategy``
            to be constructed with both ``reformulator`` and
            ``sufficiency_checker``; the strategy constructor raises
            when the flag is set but either dependency is missing.
            Defaults to False.
        max_reformulation_rounds: Maximum rounds of query reformulation
            in the Search-and-Ask loop (1-5).  Defaults to 2.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    fusion_strategy: FusionStrategy = Field(
        default=FusionStrategy.LINEAR,
        description=(
            "Ranking fusion strategy: linear for single-source "
            "relevance+recency, rrf for multi-source ranked list merging"
        ),
    )
    rrf_k: int = Field(
        default=_DEFAULT_RRF_K,
        ge=1,
        le=1000,
        description="RRF smoothing constant k (only used with RRF strategy)",
    )
    diversity_penalty_enabled: bool = Field(
        default=False,
        description=(
            "When True, apply MMR-style diversity penalty to re-rank "
            "retrieval results, reducing redundancy"
        ),
    )
    diversity_lambda: float = Field(
        default=_DEFAULT_DIVERSITY_LAMBDA,
        ge=0.0,
        le=1.0,
        description=(
            "MMR trade-off parameter: 1.0 = pure relevance (no "
            "diversity), 0.0 = maximum diversity"
        ),
    )
    candidate_pool_multiplier: int = Field(
        default=_DEFAULT_CANDIDATE_POOL_MULTIPLIER,
        ge=1,
        le=10,
        description=(
            "Over-fetch multiplier for the candidate pool when "
            "diversity_penalty_enabled is True.  The backend query "
            "fetches max_memories * candidate_pool_multiplier entries "
            "so MMR can promote diverse candidates that would "
            "otherwise fall below the top-K cutoff.  Ignored when "
            "diversity_penalty_enabled is False."
        ),
    )
    query_reformulation_enabled: bool = Field(
        default=False,
        description=(
            "Enables iterative query reformulation in the TOOL_BASED "
            "strategy.  When True, ``ToolBasedInjectionStrategy`` "
            "runs a Search-and-Ask loop (retrieve -> check sufficiency "
            "-> reformulate -> re-retrieve) up to "
            "``max_reformulation_rounds`` rounds.  Requires both "
            "``reformulator`` AND ``sufficiency_checker`` to be passed "
            "to the strategy constructor -- the constructor raises "
            "``ValueError`` when the flag is set but either collaborator "
            "is missing (fail-fast at wiring time rather than silent "
            "no-op at retrieval time).  A config-level validator also "
            "rejects this flag with strategies other than TOOL_BASED."
        ),
    )
    max_reformulation_rounds: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "Maximum rounds of query reformulation in the Search-and-Ask "
            "loop when ``query_reformulation_enabled`` is True (1-5)."
        ),
    )
    retriever: Literal["flat", "hierarchical"] = Field(
        default="flat",
        description=(
            "Retriever topology: ``flat`` uses the existing single-pass "
            "pipeline, ``hierarchical`` uses supervisor-worker routing "
            "with semantic, episodic, and procedural workers."
        ),
    )
    max_workers_per_query: int = Field(
        default=_DEFAULT_MAX_WORKERS_PER_QUERY,
        ge=1,
        le=4,
        description=(
            "Maximum workers the supervisor may invoke per query "
            "(only used when retriever is ``hierarchical``)."
        ),
    )
    reflective_retry_enabled: bool = Field(
        default=True,
        description=(
            "When True, the hierarchical supervisor evaluates result "
            "quality and retries with corrected queries on poor results."
        ),
    )
    max_retry_count: int = Field(
        default=_DEFAULT_MAX_RETRY_COUNT,
        ge=0,
        le=5,
        description=(
            "Maximum reflective retry attempts (only used when "
            "retriever is ``hierarchical`` and "
            "``reflective_retry_enabled`` is True)."
        ),
    )
    query_specific_rerank_enabled: bool = Field(
        default=False,
        description=(
            "When True, apply query-specific LLM-based re-ranking "
            "after RRF/linear fusion.  Works with both flat and "
            "hierarchical retrievers.  Adds an LLM call per retrieve."
        ),
    )
    rerank_cache_ttl_seconds: int = Field(
        default=_DEFAULT_RERANK_CACHE_TTL_SECONDS,
        ge=60,
        le=86400,
        description=(
            "TTL in seconds for the re-ranker cache "
            "(only used when ``query_specific_rerank_enabled`` is True)."
        ),
    )

    @model_validator(mode="after")
    def _validate_weight_sum(self) -> Self:
        """Ensure relevance_weight + recency_weight == 1.0 for LINEAR fusion."""
        if self.fusion_strategy != FusionStrategy.LINEAR:
            return self
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
    def _validate_rrf_k_strategy_consistency(self) -> Self:
        """Warn when rrf_k is customized but fusion strategy is LINEAR."""
        if (
            self.fusion_strategy == FusionStrategy.LINEAR
            and self.rrf_k != _DEFAULT_RRF_K
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="rrf_k",
                value=self.rrf_k,
                reason="rrf_k is ignored when fusion_strategy is LINEAR",
            )
        return self

    @model_validator(mode="after")
    def _validate_diversity_lambda_consistency(self) -> Self:
        """Warn when diversity_lambda is customized but penalty is disabled."""
        if (
            not self.diversity_penalty_enabled
            and self.diversity_lambda != _DEFAULT_DIVERSITY_LAMBDA
            and "diversity_lambda" in self.model_fields_set
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="diversity_lambda",
                value=self.diversity_lambda,
                reason=(
                    "diversity_lambda is ignored when "
                    "diversity_penalty_enabled is False"
                ),
            )
        return self

    @model_validator(mode="after")
    def _validate_diversity_strategy_consistency(self) -> Self:
        """Reject diversity penalty combined with a strategy that ignores it.

        Symmetric with ``_validate_reformulation_requires_tool_based``: a
        silent no-op is worse than a hard error because the
        misconfiguration survives deployment unnoticed.
        """
        if (
            self.diversity_penalty_enabled
            and self.strategy != InjectionStrategy.CONTEXT
        ):
            msg = (
                "diversity_penalty_enabled is only applied by "
                f"ContextInjectionStrategy; got strategy={self.strategy.value!r}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="diversity_penalty_enabled",
                value=self.diversity_penalty_enabled,
                strategy=self.strategy.value,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_reformulation_requires_tool_based(self) -> Self:
        """Query reformulation is only wired into the TOOL_BASED strategy."""
        if not self.query_reformulation_enabled:
            return self
        if self.strategy == InjectionStrategy.TOOL_BASED:
            return self
        msg = (
            "query_reformulation_enabled requires strategy='tool_based'; "
            f"got strategy={self.strategy.value!r}"
        )
        logger.warning(
            CONFIG_VALIDATION_FAILED,
            field="query_reformulation_enabled",
            value=self.query_reformulation_enabled,
            strategy=self.strategy.value,
            reason=msg,
        )
        raise ValueError(msg)

    @model_validator(mode="after")
    def _validate_personal_boost_rrf_consistency(self) -> Self:
        """Warn when personal_boost is explicitly set with RRF fusion."""
        if (
            self.fusion_strategy == FusionStrategy.RRF
            and self.personal_boost > 0.0
            and "personal_boost" in self.model_fields_set
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="personal_boost",
                value=self.personal_boost,
                reason=(
                    "personal_boost has no effect when pure RRF "
                    "fusion runs, but IS applied on the sparse-empty "
                    "fallback path that runs linear ranking"
                ),
            )
        return self

    @model_validator(mode="after")
    def _validate_pool_multiplier_consistency(self) -> Self:
        """Warn when candidate_pool_multiplier is set but diversity is off."""
        if (
            not self.diversity_penalty_enabled
            and self.candidate_pool_multiplier != _DEFAULT_CANDIDATE_POOL_MULTIPLIER
            and "candidate_pool_multiplier" in self.model_fields_set
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="candidate_pool_multiplier",
                value=self.candidate_pool_multiplier,
                reason=(
                    "candidate_pool_multiplier is ignored when "
                    "diversity_penalty_enabled is False"
                ),
            )
        return self

    @model_validator(mode="after")
    def _validate_supported_strategy(self) -> Self:
        """Reject strategies that are not yet implemented."""
        _supported = {
            InjectionStrategy.CONTEXT,
            InjectionStrategy.TOOL_BASED,
            InjectionStrategy.SELF_EDITING,
        }
        if self.strategy not in _supported:
            msg = (
                f"Strategy {self.strategy.value!r} is not yet implemented; "
                f"supported: {sorted(s.value for s in _supported)}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="strategy",
                value=self.strategy.value,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_hierarchical_requires_context(self) -> Self:
        """Hierarchical retriever only works with CONTEXT strategy."""
        if (
            self.retriever == "hierarchical"
            and self.strategy != InjectionStrategy.CONTEXT
        ):
            msg = (
                "retriever='hierarchical' requires strategy='context'; "
                f"got strategy={self.strategy.value!r}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="retriever",
                value=self.retriever,
                strategy=self.strategy.value,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_hierarchical_field_consistency(self) -> Self:
        """Warn when hierarchical fields are set but retriever is flat."""
        if self.retriever != "flat":
            return self
        if (
            "max_workers_per_query" in self.model_fields_set
            and self.max_workers_per_query != _DEFAULT_MAX_WORKERS_PER_QUERY
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="max_workers_per_query",
                value=self.max_workers_per_query,
                reason=("max_workers_per_query is ignored when retriever is 'flat'"),
            )
        if "reflective_retry_enabled" in self.model_fields_set:
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="reflective_retry_enabled",
                value=self.reflective_retry_enabled,
                reason=("reflective_retry_enabled is ignored when retriever is 'flat'"),
            )
        if (
            "max_retry_count" in self.model_fields_set
            and self.max_retry_count != _DEFAULT_MAX_RETRY_COUNT
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="max_retry_count",
                value=self.max_retry_count,
                reason=("max_retry_count is ignored when retriever is 'flat'"),
            )
        return self

    @model_validator(mode="after")
    def _validate_rerank_cache_ttl_consistency(self) -> Self:
        """Warn when rerank_cache_ttl_seconds is set but reranking off."""
        if (
            not self.query_specific_rerank_enabled
            and "rerank_cache_ttl_seconds" in self.model_fields_set
            and self.rerank_cache_ttl_seconds != _DEFAULT_RERANK_CACHE_TTL_SECONDS
        ):
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="rerank_cache_ttl_seconds",
                value=self.rerank_cache_ttl_seconds,
                reason=(
                    "rerank_cache_ttl_seconds is ignored when "
                    "query_specific_rerank_enabled is False"
                ),
            )
        return self
