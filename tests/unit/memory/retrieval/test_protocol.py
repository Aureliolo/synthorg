"""Tests for retrieval worker and hierarchical retriever protocols."""

from datetime import UTC, datetime

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry
from synthorg.memory.retrieval.models import (
    FinalRetrievalResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
)
from synthorg.memory.retrieval.protocol import (
    HierarchicalRetriever,
    RetrievalWorker,
)


def _make_entry() -> MemoryEntry:
    return MemoryEntry(
        id="mem-1",
        agent_id="agent-1",
        content="test content",
        category=MemoryCategory.SEMANTIC,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class _StubWorker:
    """Minimal RetrievalWorker implementation for protocol tests."""

    @property
    def name(self) -> str:
        return "stub"

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        candidate = RetrievalCandidate(
            entry=_make_entry(),
            relevance_score=0.8,
            combined_score=0.8,
            source_worker="stub",
        )
        return RetrievalResult(
            candidates=(candidate,),
            worker_name="stub",
            execution_ms=10,
        )


class _StubHierarchicalRetriever:
    """Minimal HierarchicalRetriever for protocol tests."""

    async def retrieve(self, query: RetrievalQuery) -> FinalRetrievalResult:
        return FinalRetrievalResult()


class TestRetrievalWorkerProtocol:
    """Tests for RetrievalWorker protocol compliance."""

    @pytest.mark.unit
    def test_stub_is_retrieval_worker(self) -> None:
        worker = _StubWorker()
        assert isinstance(worker, RetrievalWorker)

    @pytest.mark.unit
    async def test_stub_worker_retrieves(self) -> None:
        worker = _StubWorker()
        query = RetrievalQuery(text="test", agent_id="agent-1")
        result = await worker.retrieve(query)
        assert len(result.candidates) == 1
        assert result.worker_name == "stub"


class TestHierarchicalRetrieverProtocol:
    """Tests for HierarchicalRetriever protocol compliance."""

    @pytest.mark.unit
    def test_stub_is_hierarchical_retriever(self) -> None:
        retriever = _StubHierarchicalRetriever()
        assert isinstance(retriever, HierarchicalRetriever)

    @pytest.mark.unit
    async def test_stub_retriever_retrieves(self) -> None:
        retriever = _StubHierarchicalRetriever()
        query = RetrievalQuery(text="test", agent_id="agent-1")
        result = await retriever.retrieve(query)
        assert result.candidates == ()
