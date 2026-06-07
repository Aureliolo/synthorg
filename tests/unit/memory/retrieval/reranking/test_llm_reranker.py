"""Tests for LLMQuerySpecificReranker."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry
from synthorg.memory.retrieval.models import (
    RetrievalCandidate,
    RetrievalQuery,
)
from synthorg.memory.retrieval.reranking.cache import RerankerCache
from synthorg.memory.retrieval.reranking.llm_reranker import (
    LLMQuerySpecificReranker,
    _build_cache_key,
)
from synthorg.memory.retrieval.reranking.protocol import (
    QuerySpecificReranker,
)
from synthorg.providers.protocol import CompletionProvider


def _make_candidate(
    entry_id: str = "mem-1",
    score: float = 0.8,
) -> RetrievalCandidate:
    entry = MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content="test content for reranking",
        category=MemoryCategory.SEMANTIC,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return RetrievalCandidate(
        entry=entry,
        relevance_score=score,
        combined_score=score,
        source_worker="semantic",
    )


def _make_query(text: str = "test query") -> RetrievalQuery:
    return RetrievalQuery(text=text, agent_id="agent-1")


def _mock_provider(ranking: list[int]) -> AsyncMock:
    provider = AsyncMock()
    response = SimpleNamespace(
        content=json.dumps({"ranking": ranking}),
    )
    provider.complete = AsyncMock(return_value=response)
    return provider


class TestBuildCacheKey:
    """Tests for _build_cache_key helper."""

    @pytest.mark.unit
    def test_deterministic(self) -> None:
        k1 = _build_cache_key("query", ("id1", "id2"))
        k2 = _build_cache_key("query", ("id1", "id2"))
        assert k1 == k2

    @pytest.mark.unit
    def test_order_independent_for_ids(self) -> None:
        k1 = _build_cache_key("query", ("id1", "id2"))
        k2 = _build_cache_key("query", ("id2", "id1"))
        assert k1 == k2

    @pytest.mark.unit
    def test_different_queries_different_keys(self) -> None:
        k1 = _build_cache_key("query1", ("id1",))
        k2 = _build_cache_key("query2", ("id1",))
        assert k1 != k2


class TestLLMQuerySpecificReranker:
    """Tests for LLMQuerySpecificReranker."""

    @pytest.mark.unit
    def test_is_query_specific_reranker(self) -> None:
        provider = _mock_provider([0, 1])
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        assert isinstance(reranker, QuerySpecificReranker)

    @pytest.mark.unit
    async def test_rerank_reorders_candidates(self) -> None:
        provider = _mock_provider([1, 0])
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(_make_query(), (c1, c2))
        assert result[0].entry.id == "mem-2"
        assert result[1].entry.id == "mem-1"
        # Reranker preserves original combined_score; order reflects
        # LLM ranking signal, not a positional decay.
        assert result[0].combined_score == 0.7
        assert result[1].combined_score == 0.9

    @pytest.mark.unit
    async def test_rerank_single_candidate_passthrough(self) -> None:
        provider = _mock_provider([0])
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        result = await reranker.rerank(_make_query(), (c1,))
        assert result == (c1,)
        provider.complete.assert_not_awaited()

    @pytest.mark.unit
    async def test_rerank_fallback_on_llm_error(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            side_effect=RuntimeError("LLM down"),
        )
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(_make_query(), (c1, c2))
        assert result[0].entry.id == "mem-1"
        assert result[1].entry.id == "mem-2"

    @pytest.mark.unit
    async def test_rerank_fallback_on_invalid_ranking(self) -> None:
        provider = _mock_provider([0, 0])  # Invalid: duplicate indices
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(_make_query(), (c1, c2))
        # Falls back to original order
        assert result[0].entry.id == "mem-1"
        assert result[1].entry.id == "mem-2"

    @pytest.mark.unit
    async def test_rerank_fallback_on_out_of_bounds(self) -> None:
        provider = _mock_provider([0, 5])  # Invalid: index 5 out of range
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(_make_query(), (c1, c2))
        # Falls back to original order
        assert result[0].entry.id == "mem-1"
        assert result[1].entry.id == "mem-2"

    @pytest.mark.unit
    async def test_rerank_fallback_on_missing_indices(self) -> None:
        provider = _mock_provider([0])  # Invalid: missing index 1
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(_make_query(), (c1, c2))
        # Falls back to original order
        assert result[0].entry.id == "mem-1"
        assert result[1].entry.id == "mem-2"

    @pytest.mark.unit
    async def test_rerank_with_cache(self) -> None:
        provider = _mock_provider([1, 0])
        cache = RerankerCache()
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
            cache=cache,
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        query = _make_query()

        # First call: cache miss, calls LLM
        await reranker.rerank(query, (c1, c2))
        assert provider.complete.await_count == 1
        assert cache.size == 1

        # Second call with fresh candidate instances having the same
        # IDs but different combined_score values: the cache must
        # preserve the LLM ordering while reflecting fresh candidate
        # state.
        new_c1 = _make_candidate("mem-1", 0.42)
        new_c2 = _make_candidate("mem-2", 0.13)
        result2 = await reranker.rerank(query, (new_c1, new_c2))
        assert provider.complete.await_count == 1
        assert cache.size == 1
        # Cached ranking [1, 0] => expect reordered (mem-2, mem-1)
        assert [c.entry.id for c in result2] == ["mem-2", "mem-1"]

    @pytest.mark.unit
    async def test_rerank_empty_returns_empty(self) -> None:
        provider = _mock_provider([])
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        result = await reranker.rerank(_make_query(), ())
        assert result == ()

    @pytest.mark.unit
    async def test_rerank_null_content_returns_original(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        response = SimpleNamespace(content=None)
        provider.complete = AsyncMock(return_value=response)
        reranker = LLMQuerySpecificReranker(
            provider=provider,
            model="test-small-001",
        )
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(_make_query(), (c1, c2))
        assert len(result) == 2
        assert [c.entry.id for c in result] == ["mem-1", "mem-2"]
