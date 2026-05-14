"""Tests for hybrid search pipeline (RRF fusion) in ContextInjectionStrategy.

Split from ``test_retriever.py`` to keep both files under 800 lines.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.errors import MemoryRetrievalError
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import FusionStrategy
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy


def _make_entry(
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test memory",
    category: MemoryCategory = MemoryCategory.EPISODIC,
    relevance_score: float | None = 0.8,
) -> MemoryEntry:
    """Helper to build a MemoryEntry."""
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
        relevance_score=relevance_score,
    )


def _make_backend(entries: tuple[MemoryEntry, ...] = ()) -> AsyncMock:
    """Create a mock MemoryBackend (dense-only, no sparse support)."""
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=entries)
    backend.supports_sparse_search = False
    return backend


def _make_sparse_backend(
    dense_entries: tuple[MemoryEntry, ...] = (),
    sparse_entries: tuple[MemoryEntry, ...] = (),
) -> AsyncMock:
    """Create a mock backend with both retrieve and retrieve_sparse."""
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=dense_entries)
    backend.retrieve_sparse = AsyncMock(return_value=sparse_entries)
    backend.supports_sparse_search = True
    return backend


@pytest.mark.unit
class TestHybridSearchPipeline:
    async def test_rrf_merges_dense_and_sparse(self) -> None:
        """RRF fusion merges results from dense and sparse search."""
        dense_entry = _make_entry(
            entry_id="dense-1",
            content="dense result",
            relevance_score=0.9,
        )
        sparse_entry = _make_entry(
            entry_id="sparse-1",
            content="sparse result",
            relevance_score=0.7,
        )
        backend = _make_sparse_backend(
            dense_entries=(dense_entry,),
            sparse_entries=(sparse_entry,),
        )
        config = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.RRF,
            min_relevance=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        content = result[-1].content
        assert content is not None
        assert "dense result" in content
        assert "sparse result" in content

    async def test_rrf_fallback_no_sparse_backend(self) -> None:
        """When backend lacks retrieve_sparse, fuses with dense-only."""
        entry = _make_entry(content="dense only", relevance_score=0.9)
        backend = _make_backend((entry,))
        # No retrieve_sparse on this mock
        config = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.RRF,
            min_relevance=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        content = result[-1].content
        assert content is not None
        assert "dense only" in content

    async def test_rrf_min_relevance_after_fusion(self) -> None:
        """Post-RRF min_relevance filter excludes low-scoring entries."""
        high = _make_entry(
            entry_id="high-1",
            content="high score",
            relevance_score=0.9,
        )
        low = _make_entry(
            entry_id="low-1",
            content="low score",
            relevance_score=0.1,
        )
        # Dense has both; sparse has only high. RRF ranks high above low.
        backend = _make_sparse_backend(
            dense_entries=(high, low),
            sparse_entries=(high,),
        )
        config = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.RRF,
            min_relevance=0.5,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        content = result[-1].content
        assert content is not None
        assert "high score" in content
        assert "low score" not in content

    async def test_rrf_sparse_error_degrades_to_dense(self) -> None:
        """Sparse search failure degrades gracefully to dense-only."""
        entry = _make_entry(content="dense survives", relevance_score=0.9)
        backend = _make_sparse_backend(dense_entries=(entry,))
        backend.retrieve_sparse = AsyncMock(
            side_effect=MemoryRetrievalError("sparse broken"),
        )
        config = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.RRF,
            min_relevance=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        content = result[-1].content
        assert content is not None
        assert "dense survives" in content

    async def test_linear_path_unchanged(self) -> None:
        """LINEAR fusion uses existing rank_memories path."""
        entry = _make_entry(content="linear path", relevance_score=0.9)
        backend = _make_backend((entry,))
        config = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.LINEAR,
            min_relevance=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        content = result[-1].content
        assert content is not None
        assert "linear path" in content

    async def test_rrf_deduplicates_entries(self) -> None:
        """Same entry in both dense and sparse is deduplicated by RRF."""
        entry = _make_entry(
            entry_id="shared-id",
            content="appears twice",
            relevance_score=0.8,
        )
        backend = _make_sparse_backend(
            dense_entries=(entry,),
            sparse_entries=(entry,),
        )
        config = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.RRF,
            min_relevance=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        content = result[-1].content
        assert content is not None
        # Should appear only once (deduplicated)
        assert content.count("appears twice") == 1
