"""Context injection strategy -- pre-retrieves and injects memories.

Orchestrates the full retrieval pipeline: backend query → ranking →
budget-fit → format.  Implements ``MemoryInjectionStrategy`` protocol.
"""

import builtins
from datetime import UTC, datetime

import synthorg.memory.errors as memory_errors
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.filter import MemoryFilterStrategy, TagBasedMemoryFilter
from synthorg.memory.formatter import format_memory_context_with_directive
from synthorg.memory.injection import (
    DefaultTokenEstimator,
    TokenEstimator,
)
from synthorg.memory.models import MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import (
    FusionStrategy,
    ScoredMemory,
    rank_memories,
)
from synthorg.memory.ranking_mmr import apply_diversity_penalty
from synthorg.memory.retrieval.models import (
    RetrievalCandidate,
    RetrievalQuery,
)
from synthorg.memory.retrieval.protocol import HierarchicalRetriever
from synthorg.memory.retrieval.reranking.protocol import (
    QuerySpecificReranker,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever_fetch import fetch_memories
from synthorg.memory.retriever_rrf import execute_rrf_pipeline
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FILTER_INIT,
    MEMORY_RETRIEVAL_COMPLETE,
    MEMORY_RETRIEVAL_DEGRADED,
    MEMORY_RETRIEVAL_SKIPPED,
    MEMORY_RETRIEVAL_START,
)
from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)


class ContextInjectionStrategy:
    """Context injection strategy -- pre-retrieves and injects memories.

    Implements ``MemoryInjectionStrategy`` protocol.  Orchestrates
    the full pipeline: retrieve → rank → budget-fit → format.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        backend: MemoryBackend,
        config: MemoryRetrievalConfig,
        shared_store: SharedKnowledgeStore | None = None,
        token_estimator: TokenEstimator | None = None,
        memory_filter: MemoryFilterStrategy | None = None,
        hierarchical_retriever: HierarchicalRetriever | None = None,
        reranker: QuerySpecificReranker | None = None,
    ) -> None:
        """Initialise the context injection strategy.

        Args:
            backend: Memory backend for personal memories.
            config: Retrieval pipeline configuration.
            shared_store: Optional shared knowledge store.
            token_estimator: Optional custom token estimator.
            memory_filter: Optional filter applied after ranking,
                before formatting.  When ``None`` and
                ``config.non_inferable_only`` is ``True``, a
                ``TagBasedMemoryFilter`` is auto-created.  When ``None``
                and ``non_inferable_only`` is ``False``, all ranked
                memories are injected (backward-compatible).
            hierarchical_retriever: Optional hierarchical retriever
                (used when ``config.retriever == "hierarchical"``).
            reranker: Optional query-specific re-ranker (used when
                ``config.query_specific_rerank_enabled`` is ``True``).

        Raises:
            ValueError: If an argument fails domain validation.
        """
        self._backend = backend
        self._config = config
        self._shared_store = shared_store
        if memory_filter is None and config.non_inferable_only:
            memory_filter = TagBasedMemoryFilter()
        elif memory_filter is not None and config.non_inferable_only:
            logger.debug(
                MEMORY_FILTER_INIT,
                note="explicit memory_filter overrides non_inferable_only config",
                filter_strategy=getattr(memory_filter, "strategy_name", "unknown"),
            )
        self._memory_filter = memory_filter
        self._estimator = (
            token_estimator if token_estimator is not None else DefaultTokenEstimator()
        )
        if config.retriever == "hierarchical" and hierarchical_retriever is None:
            msg = "retriever='hierarchical' requires a hierarchical_retriever instance"
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline_init",
                error_type="misconfiguration",
                reason=msg,
            )
            raise ValueError(msg)
        if config.query_specific_rerank_enabled and reranker is None:
            msg = "query_specific_rerank_enabled=True requires a reranker instance"
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline_init",
                error_type="misconfiguration",
                reason=msg,
            )
            raise ValueError(msg)
        self._hierarchical_retriever = hierarchical_retriever
        self._reranker = reranker

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,
        token_budget: int,
        *,
        categories: frozenset[MemoryCategory] | None = None,
    ) -> tuple[ChatMessage, ...]:
        """Full pipeline: retrieve → rank → budget-fit → format.

        Returns empty tuple on any failure (graceful degradation).
        Never raises domain memory errors to the caller.
        Re-raises ``builtins.MemoryError`` and ``RecursionError``.

        Args:
            agent_id: The agent requesting memories.
            query_text: Text for semantic retrieval.
            token_budget: Maximum tokens for memory content.
            categories: Optional category filter.

        Returns:
            Tuple of ``ChatMessage`` instances (may be empty).

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        logger.info(
            MEMORY_RETRIEVAL_START,
            agent_id=agent_id,
            token_budget=token_budget,
        )

        if token_budget <= 0:
            logger.info(
                MEMORY_RETRIEVAL_SKIPPED,
                agent_id=agent_id,
                reason="non-positive token budget",
                token_budget=token_budget,
            )
            return ()

        try:
            return await self._execute_pipeline(
                agent_id=agent_id,
                query_text=query_text,
                token_budget=token_budget,
                categories=categories,
            )
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline",
                agent_id=agent_id,
                error_type="system",
            )
            raise
        except memory_errors.MemoryError:
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline",
                agent_id=agent_id,
            )
            return ()
        except Exception as exc:
            reraise_critical(exc)
            # ExceptionGroup may wrap system-level errors that must
            # propagate -- inspect and re-raise them.
            if isinstance(exc, ExceptionGroup):
                system_errors = exc.subgroup(
                    lambda e: isinstance(
                        e,
                        builtins.MemoryError | RecursionError,
                    ),
                )
                if system_errors is not None:
                    logger.error(
                        MEMORY_RETRIEVAL_DEGRADED,
                        source="pipeline",
                        agent_id=agent_id,
                        error_type="system_in_exception_group",
                    )
                    raise system_errors.exceptions[0] from exc
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="pipeline",
                agent_id=agent_id,
                error_type=type(exc).__qualname__,
            )
            return ()

    async def _execute_pipeline(
        self,
        *,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,
        token_budget: int,
        categories: frozenset[MemoryCategory] | None,
    ) -> tuple[ChatMessage, ...]:
        """Execute the retrieval -> rank -> filter -> diversity -> format pipeline.

        Returns:
            Tuple of ``ChatMessage``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        if (
            self._config.retriever == "hierarchical"
            and self._hierarchical_retriever is not None
        ):
            ranked = await self._execute_hierarchical_pipeline(
                agent_id=agent_id,
                query_text=query_text,
                categories=categories,
            )
        else:
            pool_limit = self._compute_pool_limit()
            query = MemoryQuery(
                text=query_text,
                categories=categories,
                limit=pool_limit,
            )
            if self._config.fusion_strategy == FusionStrategy.RRF:
                ranked = await execute_rrf_pipeline(
                    backend=self._backend,
                    shared_store=self._shared_store,
                    config=self._config,
                    agent_id=agent_id,
                    query=query,
                )
            else:
                ranked = await self._execute_linear_pipeline(
                    agent_id=agent_id,
                    query=query,
                )

        if not ranked:
            logger.info(
                MEMORY_RETRIEVAL_SKIPPED,
                agent_id=agent_id,
                reason="all below min_relevance",
            )
            return ()

        # Post-ranking: query-specific re-ranking (opt-in)
        if self._config.query_specific_rerank_enabled and self._reranker is not None:
            try:
                ranked = await self._apply_reranking(
                    query_text=query_text,
                    agent_id=agent_id,
                    ranked=ranked,
                )
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    MEMORY_RETRIEVAL_DEGRADED,
                    source="reranker",
                    agent_id=agent_id,
                    error_type=type(exc).__qualname__,
                    reason="query_specific_rerank_failed_falling_back",
                )

        ranked = self._filter_or_fail_closed(ranked, agent_id=agent_id)
        if not ranked:
            return ()
        if self._config.diversity_penalty_enabled:
            ranked = apply_diversity_penalty(
                ranked,
                diversity_lambda=self._config.diversity_lambda,
            )
            ranked = ranked[: self._config.max_memories]
        result = format_memory_context_with_directive(
            ranked,
            estimator=self._estimator,
            token_budget=token_budget,
            injection_point=self._config.injection_point,
        )
        logger.info(
            MEMORY_RETRIEVAL_COMPLETE,
            agent_id=agent_id,
            ranked_count=len(ranked),
            messages_produced=len(result),
            fusion_strategy=self._config.fusion_strategy.value,
        )
        return result

    async def _execute_hierarchical_pipeline(
        self,
        *,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,
        categories: frozenset[MemoryCategory] | None,
    ) -> tuple[ScoredMemory, ...]:
        """Delegate to hierarchical retriever and convert results.

        Returns:
            Tuple of ``ScoredMemory``.
        """
        query = RetrievalQuery(
            text=query_text,
            agent_id=agent_id,
            categories=categories,
            max_results=self._config.max_memories,
        )
        result = await self._hierarchical_retriever.retrieve(query)  # type: ignore[union-attr]
        return tuple(
            ScoredMemory(
                entry=c.entry,
                relevance_score=c.relevance_score,
                recency_score=c.recency_score,
                combined_score=c.combined_score,
                is_shared=c.is_shared,
            )
            for c in result.candidates
        )

    async def _apply_reranking(
        self,
        *,
        query_text: NotBlankStr,
        agent_id: NotBlankStr,
        ranked: tuple[ScoredMemory, ...],
    ) -> tuple[ScoredMemory, ...]:
        """Apply query-specific re-ranking to scored memories.

        Returns:
            Tuple of ``ScoredMemory``.
        """
        query = RetrievalQuery(text=query_text, agent_id=agent_id)
        candidates = tuple(
            RetrievalCandidate(
                entry=s.entry,
                relevance_score=s.relevance_score,
                recency_score=s.recency_score,
                combined_score=s.combined_score,
                source_worker="flat",
                is_shared=s.is_shared,
            )
            for s in ranked
        )
        reranked = await self._reranker.rerank(query, candidates)  # type: ignore[union-attr]
        return tuple(
            ScoredMemory(
                entry=c.entry,
                relevance_score=c.relevance_score,
                recency_score=c.recency_score,
                combined_score=c.combined_score,
                is_shared=c.is_shared,
            )
            for c in reranked
        )

    def _compute_pool_limit(self) -> int:
        """Compute the backend query limit for the candidate pool.

        When diversity penalty is enabled, over-fetches by the
        ``candidate_pool_multiplier`` so MMR can promote diverse
        candidates that would otherwise fall below the top-K cutoff.

        Returns:
            Result of type ``int``.
        """
        if self._config.diversity_penalty_enabled:
            return self._config.max_memories * self._config.candidate_pool_multiplier
        return self._config.max_memories

    def _filter_or_fail_closed(
        self,
        ranked: tuple[ScoredMemory, ...],
        *,
        agent_id: NotBlankStr,
    ) -> tuple[ScoredMemory, ...]:
        """Apply the configured memory filter, failing closed on errors.

        Runs BEFORE diversity re-ranking so entries excluded by the
        privacy/non-inferability filter are not used as MMR anchors;
        anchoring on filtered-out entries would suppress diverse but
        visible candidates textually similar to them.

        Fail-closed semantics: if the filter raises a non-system
        exception, we log at ERROR and return ``()`` rather than leak
        unfiltered memories.  System errors (``MemoryError``,
        ``RecursionError``) propagate.  When no filter is configured,
        ``ranked`` is returned unchanged.

        Returns:
            Tuple of ``ScoredMemory``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        if self._memory_filter is None:
            return ranked
        try:
            filtered = self._memory_filter.filter_for_injection(ranked)
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="memory_filter",
                agent_id=agent_id,
                error_type="system",
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="memory_filter",
                agent_id=agent_id,
                error_type=type(exc).__qualname__,
                filter_strategy=getattr(
                    self._memory_filter, "strategy_name", "unknown"
                ),
                reason="filter_failed_failing_closed",
            )
            return ()
        if not filtered:
            logger.info(
                MEMORY_RETRIEVAL_SKIPPED,
                agent_id=agent_id,
                reason="all filtered by memory filter",
            )
        return filtered

    async def _execute_linear_pipeline(
        self,
        *,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[ScoredMemory, ...]:
        """Run the LINEAR ranking pipeline (dense-only).

        Args:
            agent_id: Agent identifier.
            query: Retrieval query.

        Returns:
            Ranked and filtered memories.
        """
        personal_entries, shared_entries = await fetch_memories(
            backend=self._backend,
            shared_store=self._shared_store,
            include_shared=self._config.include_shared,
            agent_id=agent_id,
            query=query,
        )
        if not personal_entries and not shared_entries:
            return ()
        now = datetime.now(UTC)
        return rank_memories(
            personal_entries,
            config=self._config,
            now=now,
            shared_entries=shared_entries,
        )

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Context injection provides no tools.

        Returns:
            Empty tuple.
        """
        return ()

    @property
    def strategy_name(self) -> str:
        """Human-readable strategy identifier.

        Returns:
            ``"context_injection"``.
        """
        return "context_injection"
