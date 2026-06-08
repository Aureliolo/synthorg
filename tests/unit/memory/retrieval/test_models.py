"""Tests for retrieval pipeline data models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry
from synthorg.memory.retrieval.models import (
    FinalRetrievalResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
)


def _make_entry(
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test content",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        content=content,
        category=category,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_candidate(
    *,
    entry_id: str = "mem-1",
    combined_score: float = 0.8,
    source_worker: str = "semantic",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        entry=_make_entry(entry_id=entry_id),
        relevance_score=0.9,
        combined_score=combined_score,
        source_worker=source_worker,
    )


class TestRetrievalQuery:
    """Tests for RetrievalQuery model."""

    @pytest.mark.unit
    def test_minimal_construction(self) -> None:
        q = RetrievalQuery(text="find auth patterns", agent_id="agent-1")
        assert q.text == "find auth patterns"
        assert q.agent_id == "agent-1"
        assert q.categories is None
        assert q.max_results == 20
        assert q.token_budget is None

    @pytest.mark.unit
    def test_full_construction(self) -> None:
        q = RetrievalQuery(
            text="find auth patterns",
            agent_id="agent-1",
            categories=frozenset({MemoryCategory.PROCEDURAL}),
            max_results=10,
            token_budget=500,
        )
        assert q.categories == frozenset({MemoryCategory.PROCEDURAL})
        assert q.max_results == 10
        assert q.token_budget == 500

    @pytest.mark.unit
    def test_frozen(self) -> None:
        q = RetrievalQuery(text="query", agent_id="agent-1")
        with pytest.raises(ValidationError, match="frozen"):
            q.text = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_text_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="String should have at least 1 character",
        ):
            RetrievalQuery(text="", agent_id="agent-1")

    @pytest.mark.unit
    def test_whitespace_text_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            RetrievalQuery(text="   ", agent_id="agent-1")

    @pytest.mark.unit
    def test_max_results_bounds(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            RetrievalQuery(text="q", agent_id="a", max_results=0)
        with pytest.raises(ValidationError, match="less than"):
            RetrievalQuery(text="q", agent_id="a", max_results=101)

    @pytest.mark.unit
    def test_token_budget_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="greater than"):
            RetrievalQuery(text="q", agent_id="a", token_budget=0)


class TestRetrievalCandidate:
    """Tests for RetrievalCandidate model."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        c = _make_candidate()
        assert c.relevance_score == 0.9
        assert c.recency_score == 0.0
        assert c.combined_score == 0.8
        assert c.source_worker == "semantic"
        assert c.is_shared is False

    @pytest.mark.unit
    def test_score_bounds(self) -> None:
        # combined_score and relevance_score allow > 1.0 (boosted scores)
        c = _make_candidate(combined_score=1.5)
        assert c.combined_score == 1.5
        # recency_score still capped at 1.0
        with pytest.raises(ValidationError, match="less than or equal"):
            RetrievalCandidate(
                entry=_make_entry(),
                relevance_score=0.5,
                recency_score=1.1,
                combined_score=0.5,
                source_worker="semantic",
            )
        with pytest.raises(ValidationError, match="greater than or equal"):
            _make_candidate(combined_score=-0.1)

    @pytest.mark.unit
    def test_blank_source_worker_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            RetrievalCandidate(
                entry=_make_entry(),
                relevance_score=0.5,
                combined_score=0.5,
                source_worker="",
            )


class TestRetrievalResult:
    """Tests for RetrievalResult model."""

    @pytest.mark.unit
    def test_empty_result(self) -> None:
        r = RetrievalResult(worker_name="semantic")
        assert r.candidates == ()
        assert r.execution_ms == 0
        assert r.error is None

    @pytest.mark.unit
    def test_result_with_candidates(self) -> None:
        c = _make_candidate()
        r = RetrievalResult(
            candidates=(c,),
            worker_name="episodic",
            execution_ms=42,
        )
        assert len(r.candidates) == 1
        assert r.worker_name == "episodic"
        assert r.execution_ms == 42

    @pytest.mark.unit
    def test_error_result(self) -> None:
        r = RetrievalResult(
            worker_name="procedural",
            error="backend connection failed",
        )
        assert r.error == "backend connection failed"
        assert r.candidates == ()


class TestFinalRetrievalResult:
    """Tests for FinalRetrievalResult model."""

    @pytest.mark.unit
    def test_empty_result(self) -> None:
        r = FinalRetrievalResult()
        assert r.candidates == ()
        assert r.worker_results == ()
        assert r.retries_performed == 0
        assert r.rerank_applied is False

    @pytest.mark.unit
    def test_full_result(self) -> None:
        c = _make_candidate()
        wr = RetrievalResult(candidates=(c,), worker_name="semantic")
        r = FinalRetrievalResult(
            candidates=(c,),
            worker_results=(wr,),
            retries_performed=1,
            rerank_applied=True,
        )
        assert len(r.candidates) == 1
        assert len(r.worker_results) == 1
        assert r.retries_performed == 1
        assert r.rerank_applied is True
