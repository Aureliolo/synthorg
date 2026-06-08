"""Tests for retrieval workers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.retrieval.hierarchical.workers import (
    EpisodicWorker,
    ProceduralWorker,
    SemanticWorker,
)
from synthorg.memory.retrieval.models import RetrievalQuery
from synthorg.memory.retrieval.protocol import RetrievalWorker
from synthorg.memory.retrieval_config import MemoryRetrievalConfig


def _make_entry(
    entry_id: str = "mem-1",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
    relevance: float | None = 0.8,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content="test content",
        category=category,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        relevance_score=relevance,
    )


def _make_query(text: str = "test query") -> RetrievalQuery:
    return RetrievalQuery(text=text, agent_id="agent-1", max_results=10)


def _mock_backend(
    entries: tuple[MemoryEntry, ...] = (),
) -> AsyncMock:
    backend = AsyncMock()
    backend.retrieve = AsyncMock(return_value=entries)
    backend.supports_sparse_search = False
    return backend


class TestSemanticWorker:
    """Tests for SemanticWorker."""

    @pytest.mark.unit
    def test_is_retrieval_worker(self) -> None:
        backend = _mock_backend()
        config = MemoryRetrievalConfig()
        worker = SemanticWorker(backend=backend, config=config)
        assert isinstance(worker, RetrievalWorker)

    @pytest.mark.unit
    def test_name(self) -> None:
        backend = _mock_backend()
        config = MemoryRetrievalConfig()
        worker = SemanticWorker(backend=backend, config=config)
        assert worker.name == "semantic"

    @pytest.mark.unit
    async def test_retrieve_returns_candidates(self) -> None:
        entries = (_make_entry("mem-1"), _make_entry("mem-2"))
        backend = _mock_backend(entries)
        config = MemoryRetrievalConfig()
        worker = SemanticWorker(backend=backend, config=config)
        result = await worker.retrieve(_make_query())
        assert result.worker_name == "semantic"
        assert len(result.candidates) >= 1
        assert result.error is None

    @pytest.mark.unit
    async def test_retrieve_empty_backend(self) -> None:
        backend = _mock_backend(())
        config = MemoryRetrievalConfig()
        worker = SemanticWorker(backend=backend, config=config)
        result = await worker.retrieve(_make_query())
        assert result.candidates == ()
        assert result.error is None

    @pytest.mark.unit
    async def test_retrieve_on_backend_error_returns_empty_with_error(
        self,
    ) -> None:
        """Backend errors propagate to worker error isolation."""
        backend = _mock_backend()
        backend.retrieve = AsyncMock(
            side_effect=RuntimeError("connection lost"),
        )
        config = MemoryRetrievalConfig()
        worker = SemanticWorker(backend=backend, config=config)
        result = await worker.retrieve(_make_query())
        assert result.candidates == ()
        assert result.error is not None
        assert "connection lost" in result.error

    @pytest.mark.unit
    async def test_all_candidates_tagged_semantic(self) -> None:
        entries = (_make_entry("mem-1"),)
        backend = _mock_backend(entries)
        config = MemoryRetrievalConfig()
        worker = SemanticWorker(backend=backend, config=config)
        result = await worker.retrieve(_make_query())
        for c in result.candidates:
            assert c.source_worker == "semantic"


class TestEpisodicWorker:
    """Tests for EpisodicWorker."""

    @pytest.mark.unit
    def test_is_retrieval_worker(self) -> None:
        backend = _mock_backend()
        worker = EpisodicWorker(backend=backend, config=MemoryRetrievalConfig())
        assert isinstance(worker, RetrievalWorker)

    @pytest.mark.unit
    def test_name(self) -> None:
        backend = _mock_backend()
        worker = EpisodicWorker(backend=backend, config=MemoryRetrievalConfig())
        assert worker.name == "episodic"

    @pytest.mark.unit
    async def test_retrieve_filters_episodic(self) -> None:
        entries = (_make_entry("ep-1", MemoryCategory.EPISODIC),)
        backend = _mock_backend(entries)
        worker = EpisodicWorker(backend=backend, config=MemoryRetrievalConfig())
        result = await worker.retrieve(_make_query())
        assert len(result.candidates) == 1
        assert result.candidates[0].source_worker == "episodic"
        # Verify the query passed to backend includes category filter
        call_args = backend.retrieve.call_args
        mem_query: MemoryQuery = call_args[0][1]
        assert mem_query.categories == frozenset({MemoryCategory.EPISODIC})
        assert mem_query.since is not None  # time window applied

    @pytest.mark.unit
    async def test_retrieve_on_backend_error_returns_empty_with_error(
        self,
    ) -> None:
        """Backend errors propagate to worker error isolation."""
        backend = _mock_backend()
        backend.retrieve = AsyncMock(
            side_effect=RuntimeError("timeout"),
        )
        worker = EpisodicWorker(backend=backend, config=MemoryRetrievalConfig())
        result = await worker.retrieve(_make_query())
        assert result.candidates == ()
        assert result.error is not None
        assert "timeout" in result.error


class TestProceduralWorker:
    """Tests for ProceduralWorker."""

    @pytest.mark.unit
    def test_is_retrieval_worker(self) -> None:
        backend = _mock_backend()
        worker = ProceduralWorker(backend=backend, config=MemoryRetrievalConfig())
        assert isinstance(worker, RetrievalWorker)

    @pytest.mark.unit
    def test_name(self) -> None:
        backend = _mock_backend()
        worker = ProceduralWorker(backend=backend, config=MemoryRetrievalConfig())
        assert worker.name == "procedural"

    @pytest.mark.unit
    async def test_retrieve_filters_procedural(self) -> None:
        entries = (_make_entry("proc-1", MemoryCategory.PROCEDURAL),)
        backend = _mock_backend(entries)
        worker = ProceduralWorker(backend=backend, config=MemoryRetrievalConfig())
        result = await worker.retrieve(_make_query())
        assert len(result.candidates) == 1
        assert result.candidates[0].source_worker == "procedural"
        call_args = backend.retrieve.call_args
        mem_query: MemoryQuery = call_args[0][1]
        assert mem_query.categories == frozenset(
            {MemoryCategory.PROCEDURAL},
        )

    @pytest.mark.unit
    async def test_retrieve_on_backend_error_returns_empty_with_error(
        self,
    ) -> None:
        """Backend errors propagate to worker error isolation."""
        backend = _mock_backend()
        backend.retrieve = AsyncMock(
            side_effect=RuntimeError("backend down"),
        )
        worker = ProceduralWorker(backend=backend, config=MemoryRetrievalConfig())
        result = await worker.retrieve(_make_query())
        assert result.candidates == ()
        assert result.error is not None
        assert "backend down" in result.error
