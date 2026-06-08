"""Tests for QuerySpecificReranker protocol."""

from datetime import UTC, datetime

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry
from synthorg.memory.retrieval.models import (
    RetrievalCandidate,
    RetrievalQuery,
)
from synthorg.memory.retrieval.reranking.protocol import (
    QuerySpecificReranker,
)


def _make_candidate(
    entry_id: str = "mem-1",
    score: float = 0.8,
) -> RetrievalCandidate:
    entry = MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content="test",
        category=MemoryCategory.SEMANTIC,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return RetrievalCandidate(
        entry=entry,
        relevance_score=score,
        combined_score=score,
        source_worker="semantic",
    )


class _StubReranker:
    """Minimal reranker that reverses candidate order."""

    async def rerank(
        self,
        query: RetrievalQuery,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RetrievalCandidate, ...]:
        return tuple(reversed(candidates))


class TestQuerySpecificRerankerProtocol:
    """Tests for QuerySpecificReranker protocol compliance."""

    @pytest.mark.unit
    def test_stub_is_reranker(self) -> None:
        reranker = _StubReranker()
        assert isinstance(reranker, QuerySpecificReranker)

    @pytest.mark.unit
    async def test_stub_reranker_reverses(self) -> None:
        reranker = _StubReranker()
        query = RetrievalQuery(text="test", agent_id="agent-1")
        c1 = _make_candidate("mem-1", 0.9)
        c2 = _make_candidate("mem-2", 0.7)
        result = await reranker.rerank(query, (c1, c2))
        assert result[0].entry.id == "mem-2"
        assert result[1].entry.id == "mem-1"
