"""Tests for DefaultHierarchicalRetriever."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry
from synthorg.memory.retrieval.hierarchical.default_retriever import (
    DefaultHierarchicalRetriever,
    _deduplicate_candidates,
)
from synthorg.memory.retrieval.hierarchical.models import (
    RetrievalRetryCorrection,
    WorkerRoutingDecision,
)
from synthorg.memory.retrieval.models import (
    FinalRetrievalResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
)
from synthorg.memory.retrieval.protocol import HierarchicalRetriever
from synthorg.memory.retrieval_config import MemoryRetrievalConfig


def _make_entry(entry_id: str = "mem-1") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content="test content",
        category=MemoryCategory.SEMANTIC,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_candidate(
    entry_id: str = "mem-1",
    score: float = 0.8,
    worker: str = "semantic",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        entry=_make_entry(entry_id),
        relevance_score=score,
        combined_score=score,
        source_worker=worker,
    )


def _make_query() -> RetrievalQuery:
    return RetrievalQuery(text="test", agent_id="agent-1", max_results=10)


def _stub_worker(
    name: str,
    candidates: tuple[RetrievalCandidate, ...] = (),
) -> AsyncMock:
    worker = AsyncMock()
    worker.name = name
    worker.retrieve = AsyncMock(
        return_value=RetrievalResult(
            candidates=candidates,
            worker_name=name,
            execution_ms=5,
        ),
    )
    return worker


def _stub_supervisor(
    workers: tuple[str, ...] = ("semantic",),
    retry_correction: RetrievalRetryCorrection | None = None,
) -> AsyncMock:
    supervisor = AsyncMock()
    supervisor.route = AsyncMock(
        return_value=WorkerRoutingDecision(
            selected_workers=workers,
            reason="test routing",
        ),
    )
    supervisor.reflective_retry_enabled = retry_correction is not None
    supervisor.max_retry_count = 1
    supervisor.evaluate_for_retry = AsyncMock(
        return_value=retry_correction,
    )
    return supervisor


class TestDeduplicateCandidates:
    """Tests for _deduplicate_candidates helper."""

    @pytest.mark.unit
    def test_deduplicates_by_entry_id(self) -> None:
        c1 = _make_candidate("mem-1", 0.8)
        c2 = _make_candidate("mem-1", 0.9)
        result = _deduplicate_candidates((c1, c2), max_results=10)
        assert len(result) == 1
        assert result[0].combined_score == 0.9

    @pytest.mark.unit
    def test_sorts_descending(self) -> None:
        c1 = _make_candidate("mem-1", 0.5)
        c2 = _make_candidate("mem-2", 0.9)
        result = _deduplicate_candidates((c1, c2), max_results=10)
        assert result[0].entry.id == "mem-2"
        assert result[1].entry.id == "mem-1"

    @pytest.mark.unit
    def test_truncates_to_max(self) -> None:
        candidates = tuple(
            _make_candidate(f"mem-{i}", 0.5 + i * 0.01) for i in range(5)
        )
        result = _deduplicate_candidates(candidates, max_results=3)
        assert len(result) == 3

    @pytest.mark.unit
    def test_empty_input(self) -> None:
        result = _deduplicate_candidates((), max_results=10)
        assert result == ()


class TestDefaultHierarchicalRetriever:
    """Tests for DefaultHierarchicalRetriever."""

    @pytest.mark.unit
    def test_is_hierarchical_retriever(self) -> None:
        supervisor = _stub_supervisor()
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={"semantic": _stub_worker("semantic")},
            config=config,
        )
        assert isinstance(retriever, HierarchicalRetriever)

    @pytest.mark.unit
    async def test_retrieve_routes_to_workers(self) -> None:
        c1 = _make_candidate("mem-1", 0.9, "semantic")
        semantic = _stub_worker("semantic", (c1,))
        supervisor = _stub_supervisor(("semantic",))
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={"semantic": semantic},
            config=config,
        )
        result = await retriever.retrieve(_make_query())
        assert len(result.candidates) == 1
        assert result.candidates[0].entry.id == "mem-1"
        semantic.retrieve.assert_awaited_once()

    @pytest.mark.unit
    async def test_retrieve_multiple_workers(self) -> None:
        c1 = _make_candidate("mem-1", 0.9, "semantic")
        c2 = _make_candidate("mem-2", 0.7, "episodic")
        semantic = _stub_worker("semantic", (c1,))
        episodic = _stub_worker("episodic", (c2,))
        supervisor = _stub_supervisor(("semantic", "episodic"))
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={
                "semantic": semantic,
                "episodic": episodic,
            },
            config=config,
        )
        result = await retriever.retrieve(_make_query())
        assert len(result.candidates) == 2
        assert (
            result.candidates[0].combined_score >= result.candidates[1].combined_score
        )

    @pytest.mark.unit
    async def test_retrieve_deduplicates_across_workers(self) -> None:
        c1 = _make_candidate("mem-1", 0.9, "semantic")
        c2 = _make_candidate("mem-1", 0.7, "episodic")
        semantic = _stub_worker("semantic", (c1,))
        episodic = _stub_worker("episodic", (c2,))
        supervisor = _stub_supervisor(("semantic", "episodic"))
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={
                "semantic": semantic,
                "episodic": episodic,
            },
            config=config,
        )
        result = await retriever.retrieve(_make_query())
        assert len(result.candidates) == 1
        assert result.candidates[0].combined_score == 0.9

    @pytest.mark.unit
    async def test_retrieve_handles_worker_error(self) -> None:
        semantic = _stub_worker("semantic")
        semantic.retrieve = AsyncMock(
            side_effect=RuntimeError("worker failed"),
        )
        supervisor = _stub_supervisor(("semantic",))
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={"semantic": semantic},
            config=config,
        )
        result = await retriever.retrieve(_make_query())
        assert result.candidates == ()
        assert len(result.worker_results) == 1
        assert result.worker_results[0].error is not None

    @pytest.mark.unit
    async def test_retrieve_with_retry(self) -> None:
        c1 = _make_candidate("mem-1", 0.1, "semantic")
        c2 = _make_candidate("mem-2", 0.9, "semantic")
        semantic = _stub_worker("semantic", (c1,))
        # On retry, return better results
        semantic.retrieve = AsyncMock(
            side_effect=[
                RetrievalResult(
                    candidates=(c1,),
                    worker_name="semantic",
                    execution_ms=5,
                ),
                RetrievalResult(
                    candidates=(c2,),
                    worker_name="semantic",
                    execution_ms=5,
                ),
            ],
        )
        # Provide a corrected query so the retry is not a no-op
        # (identical (query, workers) pairs are rejected to prevent
        # infinite loops).
        corrected = _make_query().model_copy(
            update={"text": "corrected query text"},
        )
        correction = RetrievalRetryCorrection(
            reason="Results too low quality",
            corrected_query=corrected,
        )
        supervisor = _stub_supervisor(
            ("semantic",),
            retry_correction=correction,
        )
        # After first evaluate, return None to stop retry loop
        supervisor.evaluate_for_retry = AsyncMock(
            side_effect=[correction, None],
        )
        config = MemoryRetrievalConfig(retriever="hierarchical")
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={"semantic": semantic},
            config=config,
        )
        result = await retriever.retrieve(_make_query())
        assert result.retries_performed >= 1
        # Worker was invoked twice (initial + 1 retry)
        assert semantic.retrieve.await_count == 2
        # Best candidate from retry (c2 with 0.9) wins over c1 (0.1)
        # after dedup on entry.id / highest combined_score.
        candidate_ids = [c.entry.id for c in result.candidates]
        assert "mem-2" in candidate_ids
        mem2 = next(c for c in result.candidates if c.entry.id == "mem-2")
        assert mem2.combined_score == 0.9

    @pytest.mark.unit
    async def test_retrieve_empty_workers_returns_empty(self) -> None:
        semantic_candidate = _make_candidate("mem-sem", 0.8, "semantic")
        supervisor = _stub_supervisor(("nonexistent",))
        retriever = DefaultHierarchicalRetriever(
            supervisor=supervisor,
            workers={
                "semantic": _stub_worker("semantic", (semantic_candidate,)),
            },
            config=MemoryRetrievalConfig(retriever="hierarchical"),
        )
        result = await retriever.retrieve(_make_query())
        # nonexistent worker filtered out, falls back to semantic
        assert isinstance(result, FinalRetrievalResult)
        worker_names = {wr.worker_name for wr in result.worker_results}
        assert worker_names == {"semantic"}
        assert "nonexistent" not in worker_names
        assert any(c.entry.id == "mem-sem" for c in result.candidates)
