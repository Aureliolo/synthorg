"""Retrieval workers -- one per memory scope.

Each worker wraps ``MemoryBackend`` with scope-specific filtering
and scoring.  Workers implement the ``RetrievalWorker`` protocol.
"""

import builtins
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory
from synthorg.memory import errors as memory_errors
from synthorg.memory.models import MemoryQuery
from synthorg.memory.ranking import (
    FusionStrategy,
    ScoredMemory,
    fuse_ranked_lists,
    rank_memories,
)
from synthorg.memory.retrieval.models import (
    RetrievalCandidate,
    RetrievalResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_HIERARCHICAL_WORKER_COMPLETE,
    MEMORY_HIERARCHICAL_WORKER_DEGRADED,
    MEMORY_HIERARCHICAL_WORKER_FAILED,
    MEMORY_HIERARCHICAL_WORKER_START,
)

if TYPE_CHECKING:
    from synthorg.memory.models import MemoryEntry
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.memory.retrieval.models import RetrievalQuery
    from synthorg.memory.retrieval_config import MemoryRetrievalConfig
    from synthorg.memory.shared import SharedKnowledgeStore

logger = get_logger(__name__)

_DEFAULT_EPISODIC_WINDOW_HOURS = 72


def _scored_to_candidate(
    scored: ScoredMemory,
    *,
    source_worker: str,
) -> RetrievalCandidate:
    """Convert a ``ScoredMemory`` to a ``RetrievalCandidate``."""
    return RetrievalCandidate(
        entry=scored.entry,
        relevance_score=scored.relevance_score,
        recency_score=scored.recency_score,
        combined_score=scored.combined_score,
        source_worker=source_worker,
        is_shared=scored.is_shared,
    )


async def _safe_retrieve(
    backend: MemoryBackend,
    agent_id: str,
    query: MemoryQuery,
    *,
    worker: str,
) -> tuple[MemoryEntry, ...]:
    """Retrieve from backend, returning ``()`` on domain errors.

    Only domain-specific ``MemoryError`` (from ``memory.errors``) is
    swallowed as a non-fatal empty result.  Unexpected exceptions
    propagate so the caller's error isolation captures them.

    Args:
        backend: Memory backend to query.
        agent_id: Agent identifier for scoped retrieval.
        query: Query parameters.
        worker: Worker name for observability attribution (so
            telemetry can distinguish semantic/episodic/procedural
            backend degradations rather than collapsing them into
            a single ``_safe_retrieve`` bucket).
    """
    try:
        return await backend.retrieve(agent_id, query)
    except builtins.MemoryError, RecursionError:
        raise
    except memory_errors.MemoryError as exc:
        # Domain-level retrieval failure: log as degradation so the
        # empty result is distinguishable from "no memories matched".
        logger.warning(
            MEMORY_HIERARCHICAL_WORKER_DEGRADED,
            worker=worker,
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


class SemanticWorker:
    """Full-spectrum semantic worker using RRF or linear ranking.

    Wraps the existing ``MemoryBackend`` dense + optional sparse
    retrieval pipeline.  No category filter -- searches across all
    memory types (same as the flat pipeline).

    Args:
        backend: Memory backend for personal memories.
        config: Retrieval pipeline configuration.
        shared_store: Optional shared knowledge store.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: MemoryRetrievalConfig,
        shared_store: SharedKnowledgeStore | None = None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._shared_store = shared_store

    @property
    def name(self) -> str:
        """Worker identifier."""
        return "semantic"

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Execute semantic retrieval using dense + optional sparse."""
        start = time.monotonic()
        logger.info(
            MEMORY_HIERARCHICAL_WORKER_START,
            worker=self.name,
            query_length=len(query.text),
        )
        try:
            mem_query = MemoryQuery(
                text=query.text,
                categories=query.categories,
                limit=query.max_results,
            )
            personal = await _safe_retrieve(
                self._backend,
                query.agent_id,
                mem_query,
                worker=self.name,
            )
            shared: tuple[MemoryEntry, ...] = ()
            if self._shared_store is not None and self._config.include_shared:
                try:
                    shared = await self._shared_store.search_shared(
                        mem_query,
                        exclude_agent=query.agent_id,
                    )
                except builtins.MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    # Shared store failure is a partial degradation:
                    # the worker can still return personal results, so
                    # emit DEGRADED (not FAILED) to avoid overcounting
                    # worker failures in telemetry.
                    logger.warning(
                        MEMORY_HIERARCHICAL_WORKER_DEGRADED,
                        worker=self.name,
                        source="shared_store",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    shared = ()

            if self._config.fusion_strategy == FusionStrategy.RRF:
                ranked = self._rank_rrf(
                    personal,
                    shared,
                    max_results=query.max_results,
                )
            else:
                ranked = self._rank_linear(
                    personal,
                    shared,
                    max_results=query.max_results,
                )

            candidates = tuple(
                _scored_to_candidate(s, source_worker=self.name) for s in ranked
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                MEMORY_HIERARCHICAL_WORKER_COMPLETE,
                worker=self.name,
                candidate_count=len(candidates),
                elapsed_ms=elapsed_ms,
            )
            return RetrievalResult(
                candidates=candidates,
                worker_name=self.name,
                execution_ms=elapsed_ms,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                MEMORY_HIERARCHICAL_WORKER_FAILED,
                worker=self.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                elapsed_ms=elapsed_ms,
            )
            return RetrievalResult(
                worker_name=self.name,
                execution_ms=elapsed_ms,
                error=str(exc),
            )

    def _rank_rrf(
        self,
        personal: tuple[MemoryEntry, ...],
        shared: tuple[MemoryEntry, ...],
        *,
        max_results: int,
    ) -> tuple[ScoredMemory, ...]:
        """Rank via RRF fusion over personal + shared lists."""
        lists: list[tuple[MemoryEntry, ...]] = []
        if personal:
            lists.append(personal)
        if shared:
            lists.append(shared)
        if not lists:
            return ()
        return fuse_ranked_lists(
            tuple(lists),
            k=self._config.rrf_k,
            max_results=max_results,
        )

    def _rank_linear(
        self,
        personal: tuple[MemoryEntry, ...],
        shared: tuple[MemoryEntry, ...],
        *,
        max_results: int,
    ) -> tuple[ScoredMemory, ...]:
        """Rank via linear combination of relevance + recency."""
        effective_config = self._config.model_copy(
            update={"max_memories": max_results},
        )
        return rank_memories(
            personal,
            config=effective_config,
            now=datetime.now(UTC),
            shared_entries=shared or (),
        )


class EpisodicWorker:
    """Time-windowed episodic memory worker.

    Retrieves recent EPISODIC memories within a configurable time
    window and ranks by recency.

    Args:
        backend: Memory backend for personal memories.
        config: Retrieval pipeline configuration (for
            ``default_relevance`` fallback).
        time_window_hours: Lookback window in hours.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: MemoryRetrievalConfig,
        time_window_hours: int = _DEFAULT_EPISODIC_WINDOW_HOURS,
    ) -> None:
        if time_window_hours <= 0:
            msg = f"time_window_hours must be positive, got {time_window_hours}"
            raise ValueError(msg)
        self._backend = backend
        self._config = config
        self._time_window_hours = time_window_hours

    @property
    def name(self) -> str:
        """Worker identifier."""
        return "episodic"

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve recent episodic memories."""
        start = time.monotonic()
        logger.info(
            MEMORY_HIERARCHICAL_WORKER_START,
            worker=self.name,
            query_length=len(query.text),
        )
        # Respect caller's category filter: skip entirely if EPISODIC
        # is excluded by the query.
        if (
            query.categories is not None
            and MemoryCategory.EPISODIC not in query.categories
        ):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return RetrievalResult(
                worker_name=self.name,
                execution_ms=elapsed_ms,
            )
        try:
            since = datetime.now(UTC) - timedelta(
                hours=self._time_window_hours,
            )
            # Fetch a wider pool than ``query.max_results`` so the
            # recency-aware ranking below can see the full time
            # window before truncation.  A 4x multiplier matches the
            # candidate pool multiplier used by the flat retriever.
            pool_limit = max(query.max_results * 4, query.max_results)
            mem_query = MemoryQuery(
                text=query.text,
                categories=frozenset({MemoryCategory.EPISODIC}),
                since=since,
                limit=pool_limit,
            )
            entries = await _safe_retrieve(
                self._backend,
                query.agent_id,
                mem_query,
                worker=self.name,
            )
            now = datetime.now(UTC)
            window_seconds = self._time_window_hours * 3600
            candidates_list: list[RetrievalCandidate] = []
            fallback_relevance = self._config.default_relevance
            for e in entries:
                relevance = (
                    e.relevance_score
                    if e.relevance_score is not None
                    else fallback_relevance
                )
                recency = min(
                    1.0,
                    max(
                        0.0,
                        1.0
                        - (now - e.created_at).total_seconds() / max(window_seconds, 1),
                    ),
                )
                candidates_list.append(
                    RetrievalCandidate(
                        entry=e,
                        relevance_score=relevance,
                        recency_score=recency,
                        combined_score=0.4 * relevance + 0.6 * recency,
                        source_worker=self.name,
                    )
                )
            # Sort by combined_score (descending), then trim to the
            # caller's requested max_results.  Trimming AFTER ranking
            # ensures recent-but-low-relevance items can compete for
            # slots against older-but-high-relevance items.
            candidates_list.sort(
                key=lambda c: c.combined_score,
                reverse=True,
            )
            candidates = tuple(candidates_list[: query.max_results])
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                MEMORY_HIERARCHICAL_WORKER_COMPLETE,
                worker=self.name,
                candidate_count=len(candidates),
                elapsed_ms=elapsed_ms,
            )
            return RetrievalResult(
                candidates=candidates,
                worker_name=self.name,
                execution_ms=elapsed_ms,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                MEMORY_HIERARCHICAL_WORKER_FAILED,
                worker=self.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                elapsed_ms=elapsed_ms,
            )
            return RetrievalResult(
                worker_name=self.name,
                execution_ms=elapsed_ms,
                error=str(exc),
            )


class ProceduralWorker:
    """Procedural memory worker (skills, how-to patterns).

    Retrieves PROCEDURAL memories filtered by category.

    Args:
        backend: Memory backend for personal memories.
        config: Retrieval pipeline configuration (for
            ``default_relevance`` fallback).
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: MemoryRetrievalConfig,
    ) -> None:
        self._backend = backend
        self._config = config

    @property
    def name(self) -> str:
        """Worker identifier."""
        return "procedural"

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve procedural memories."""
        start = time.monotonic()
        logger.info(
            MEMORY_HIERARCHICAL_WORKER_START,
            worker=self.name,
            query_length=len(query.text),
        )
        # Respect caller's category filter: skip if PROCEDURAL excluded.
        if (
            query.categories is not None
            and MemoryCategory.PROCEDURAL not in query.categories
        ):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return RetrievalResult(
                worker_name=self.name,
                execution_ms=elapsed_ms,
            )
        try:
            mem_query = MemoryQuery(
                text=query.text,
                categories=frozenset({MemoryCategory.PROCEDURAL}),
                limit=query.max_results,
            )
            entries = await _safe_retrieve(
                self._backend,
                query.agent_id,
                mem_query,
                worker=self.name,
            )
            candidates_list: list[RetrievalCandidate] = []
            fallback_relevance = self._config.default_relevance
            for e in entries:
                relevance = (
                    e.relevance_score
                    if e.relevance_score is not None
                    else fallback_relevance
                )
                candidates_list.append(
                    RetrievalCandidate(
                        entry=e,
                        relevance_score=relevance,
                        combined_score=relevance,
                        source_worker=self.name,
                    )
                )
            candidates = tuple(candidates_list)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                MEMORY_HIERARCHICAL_WORKER_COMPLETE,
                worker=self.name,
                candidate_count=len(candidates),
                elapsed_ms=elapsed_ms,
            )
            return RetrievalResult(
                candidates=candidates,
                worker_name=self.name,
                execution_ms=elapsed_ms,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                MEMORY_HIERARCHICAL_WORKER_FAILED,
                worker=self.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                elapsed_ms=elapsed_ms,
            )
            return RetrievalResult(
                worker_name=self.name,
                execution_ms=elapsed_ms,
                error=str(exc),
            )
