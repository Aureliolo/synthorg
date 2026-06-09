"""Reciprocal-rank-fusion hybrid retrieval pipeline.

Fetches dense and sparse candidates in parallel, fuses them via
``fuse_ranked_lists()``, and applies the ``min_relevance`` post-filter.
``ContextInjectionStrategy`` delegates its RRF branch to
``execute_rrf_pipeline``.
"""

import asyncio
import builtins
from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import ScoredMemory, rank_memories
from synthorg.memory.ranking_rrf import fuse_ranked_lists
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever_fetch import (
    fetch_memories,
    fetch_sparse_memories,
)
from synthorg.memory.shared import SharedKnowledgeStore


async def execute_rrf_pipeline(
    *,
    backend: MemoryBackend,
    shared_store: SharedKnowledgeStore | None,
    config: MemoryRetrievalConfig,
    agent_id: NotBlankStr,
    query: MemoryQuery,
) -> tuple[ScoredMemory, ...]:
    """Run the RRF hybrid search pipeline (dense + sparse).

    Fetches dense and sparse results in parallel, merges via
    ``fuse_ranked_lists()``, and applies the ``min_relevance``
    post-filter (RRF does not filter internally). Falls back to linear
    ranking when the sparse arm returns nothing.

    Args:
        backend: Memory backend for personal memories.
        shared_store: Optional shared knowledge store.
        config: Retrieval pipeline configuration.
        agent_id: Agent identifier.
        query: Retrieval query.

    Returns:
        Fused, filtered, and truncated memories.

    Raises:
        builtins.MemoryError: Re-raised (system-level).
        RecursionError: Re-raised (system-level).
    """
    dense_coro = fetch_memories(
        backend=backend,
        shared_store=shared_store,
        include_shared=config.include_shared,
        agent_id=agent_id,
        query=query,
    )
    sparse_coro = fetch_sparse_memories(
        backend=backend,
        agent_id=agent_id,
        query=query,
    )
    try:
        async with asyncio.TaskGroup() as tg:
            dense_task = tg.create_task(dense_coro)
            sparse_task = tg.create_task(sparse_coro)
    except* builtins.MemoryError as eg:
        raise eg.exceptions[0] from eg
    except* RecursionError as eg:
        raise eg.exceptions[0] from eg

    dense_personal, dense_shared = dense_task.result()
    sparse_personal, sparse_shared = sparse_task.result()

    # When sparse is empty, fall back to linear ranking instead
    # of running RRF on a single dense list.
    if not sparse_personal and not sparse_shared:
        now = datetime.now(UTC)
        return rank_memories(
            dense_personal,
            config=config,
            now=now,
            shared_entries=dense_shared,
        )

    return _merge_and_fuse(
        dense_personal + dense_shared,
        sparse_personal + sparse_shared,
        config=config,
    )


def _merge_and_fuse(
    dense_entries: tuple[MemoryEntry, ...],
    sparse_entries: tuple[MemoryEntry, ...],
    *,
    config: MemoryRetrievalConfig,
) -> tuple[ScoredMemory, ...]:
    """Sort modalities by relevance, fuse via RRF, and filter.

    Args:
        dense_entries: Combined personal + shared dense results.
        sparse_entries: Combined personal + shared sparse results.
        config: Retrieval pipeline configuration.

    Returns:
        Fused, filtered, and truncated memories.
    """
    # Sort by relevance so RRF rank reflects quality, not source order.
    dense_list = tuple(
        sorted(
            dense_entries,
            key=lambda e: e.relevance_score or 0.0,
            reverse=True,
        )
    )
    sparse_list = tuple(
        sorted(
            sparse_entries,
            key=lambda e: e.relevance_score or 0.0,
            reverse=True,
        )
    )

    if not dense_list and not sparse_list:
        return ()

    ranked = fuse_ranked_lists(
        (dense_list, sparse_list),
        k=config.rrf_k,
        max_results=config.max_memories,
    )

    # Post-RRF min_relevance filter (fuse_ranked_lists doesn't filter).
    return tuple(s for s in ranked if s.combined_score >= config.min_relevance)
