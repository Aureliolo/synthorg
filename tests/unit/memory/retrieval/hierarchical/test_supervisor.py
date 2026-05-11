"""Tests for SupervisorRouter."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import MemoryEntry
from synthorg.memory.retrieval.hierarchical.models import (
    RetrievalRetryCorrection,
    WorkerRoutingDecision,
)
from synthorg.memory.retrieval.hierarchical.supervisor import (
    SupervisorRouter,
)
from synthorg.memory.retrieval.models import (
    FinalRetrievalResult,
    RetrievalCandidate,
    RetrievalQuery,
)
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of


def _make_query(text: str = "how do I implement auth?") -> RetrievalQuery:
    return RetrievalQuery(text=text, agent_id="agent-1")


def _make_entry(entry_id: str = "mem-1") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content="test",
        category=MemoryCategory.SEMANTIC,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_candidate(
    score: float = 0.8,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        entry=_make_entry(),
        relevance_score=score,
        combined_score=score,
        source_worker="semantic",
    )


def _mock_provider(response_content: str) -> AsyncMock:
    from types import SimpleNamespace

    provider = AsyncMock()
    response = SimpleNamespace(content=response_content)
    provider.complete = AsyncMock(return_value=response)
    return provider


class TestSupervisorRouterRouting:
    """Tests for routing decisions."""

    @pytest.mark.unit
    async def test_route_returns_valid_workers(self) -> None:
        provider = _mock_provider(
            json.dumps({"workers": ["semantic", "episodic"], "reason": "mixed query"}),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        decision = await supervisor.route(_make_query())
        assert isinstance(decision, WorkerRoutingDecision)
        assert "semantic" in decision.selected_workers
        assert "episodic" in decision.selected_workers

    @pytest.mark.unit
    async def test_route_filters_invalid_workers(self) -> None:
        provider = _mock_provider(
            json.dumps({"workers": ["semantic", "invalid_worker"], "reason": "test"}),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        decision = await supervisor.route(_make_query())
        assert "invalid_worker" not in decision.selected_workers
        assert "semantic" in decision.selected_workers

    @pytest.mark.unit
    async def test_route_respects_max_workers(self) -> None:
        provider = _mock_provider(
            json.dumps(
                {
                    "workers": ["semantic", "episodic", "procedural"],
                    "reason": "all",
                }
            ),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
            max_workers_per_query=2,
        )
        decision = await supervisor.route(_make_query())
        assert len(decision.selected_workers) <= 2

    @pytest.mark.unit
    async def test_route_fallback_on_llm_error(self) -> None:
        provider = mock_of[CompletionProvider]()
        provider.complete = AsyncMock(
            side_effect=RuntimeError("LLM unavailable"),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        decision = await supervisor.route(_make_query())
        assert decision.selected_workers == ("semantic",)
        assert "fallback" in decision.reason.lower()

    @pytest.mark.unit
    async def test_route_fallback_on_malformed_json(self) -> None:
        provider = _mock_provider("not valid json at all")
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        decision = await supervisor.route(_make_query())
        assert decision.selected_workers == ("semantic",)
        assert "fallback" in decision.reason.lower()

    @pytest.mark.unit
    async def test_evaluate_fallback_on_malformed_json(self) -> None:
        provider = _mock_provider("not valid json")
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        result = FinalRetrievalResult(
            candidates=(_make_candidate(0.1),),
        )
        correction = await supervisor.evaluate_for_retry(
            _make_query(),
            result,
        )
        assert correction is None

    @pytest.mark.unit
    async def test_route_fallback_on_empty_response(self) -> None:
        provider = _mock_provider(
            json.dumps({"workers": [], "reason": "none"}),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        decision = await supervisor.route(_make_query())
        assert decision.selected_workers == ("semantic",)


class TestSupervisorRouterRetry:
    """Tests for retry evaluation."""

    @pytest.mark.unit
    async def test_evaluate_returns_none_when_disabled(self) -> None:
        provider = _mock_provider("{}")
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
            reflective_retry_enabled=False,
        )
        result = FinalRetrievalResult(
            candidates=(_make_candidate(0.5),),
        )
        correction = await supervisor.evaluate_for_retry(
            _make_query(),
            result,
        )
        assert correction is None

    @pytest.mark.unit
    async def test_evaluate_returns_correction_on_empty(self) -> None:
        provider = _mock_provider("{}")
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        result = FinalRetrievalResult(candidates=())
        correction = await supervisor.evaluate_for_retry(
            _make_query(),
            result,
        )
        assert correction is not None
        assert correction.alternative_strategy == "semantic_only"

    @pytest.mark.unit
    async def test_evaluate_returns_none_when_quality_ok(self) -> None:
        provider = _mock_provider("{}")
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        result = FinalRetrievalResult(
            candidates=(_make_candidate(0.9),),
        )
        correction = await supervisor.evaluate_for_retry(
            _make_query(),
            result,
        )
        assert correction is None

    @pytest.mark.unit
    async def test_evaluate_calls_llm_on_low_quality(self) -> None:
        provider = _mock_provider(
            json.dumps(
                {
                    "retry": True,
                    "corrected_query": "broader auth search",
                    "alternative_strategy": None,
                    "reason": "Query too narrow",
                }
            ),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        result = FinalRetrievalResult(
            candidates=(_make_candidate(0.1),),
        )
        correction = await supervisor.evaluate_for_retry(
            _make_query(),
            result,
        )
        assert correction is not None
        assert isinstance(correction, RetrievalRetryCorrection)
        assert correction.corrected_query is not None
        assert correction.corrected_query.text == "broader auth search"

    @pytest.mark.unit
    async def test_evaluate_fallback_on_llm_error(self) -> None:
        provider = mock_of[CompletionProvider]()
        provider.complete = AsyncMock(
            side_effect=RuntimeError("LLM error"),
        )
        supervisor = SupervisorRouter(
            provider=provider,
            model="test-small-001",
        )
        result = FinalRetrievalResult(
            candidates=(_make_candidate(0.1),),
        )
        correction = await supervisor.evaluate_for_retry(
            _make_query(),
            result,
        )
        assert correction is None
