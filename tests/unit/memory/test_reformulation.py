"""Tests for agentic query reformulation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.reformulation import (
    LLMQueryReformulator,
    LLMSufficiencyChecker,
    QueryReformulator,
    SufficiencyChecker,
)


def _make_entry(
    *,
    content: str = "test memory",
    relevance_score: float | None = 0.8,
) -> MemoryEntry:
    return MemoryEntry(
        id="mem-1",
        agent_id="agent-1",
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
        relevance_score=relevance_score,
    )


def _mock_completion(response: str) -> AsyncMock:
    """Create a mock completion callback returning fixed text."""
    return AsyncMock(return_value=response)


# ── Protocol compliance ──────────────────────────────────────────


@pytest.mark.unit
class TestProtocolCompliance:
    def test_reformulator_satisfies_protocol(self) -> None:
        reformulator = LLMQueryReformulator(
            completion_fn=_mock_completion("rewritten"),
        )
        assert isinstance(reformulator, QueryReformulator)

    def test_sufficiency_checker_satisfies_protocol(self) -> None:
        checker = LLMSufficiencyChecker(
            completion_fn=_mock_completion("SUFFICIENT"),
        )
        assert isinstance(checker, SufficiencyChecker)


# ── LLMQueryReformulator ─────────────────────────────────────────


@pytest.mark.unit
class TestLLMQueryReformulator:
    async def test_reformulates_query(self) -> None:
        reformulator = LLMQueryReformulator(
            completion_fn=_mock_completion("authentication JWT tokens decision"),
        )
        entries = (_make_entry(content="unrelated content"),)
        result = await reformulator.reformulate("auth approach", entries)
        assert result is not None
        assert result != "auth approach"

    async def test_returns_none_on_empty_response(self) -> None:
        reformulator = LLMQueryReformulator(
            completion_fn=_mock_completion(""),
        )
        result = await reformulator.reformulate("query", ())
        assert result is None

    async def test_completion_fn_called(self) -> None:
        mock_fn = _mock_completion("expanded query terms")
        reformulator = LLMQueryReformulator(completion_fn=mock_fn)
        await reformulator.reformulate("original", ())
        mock_fn.assert_awaited_once()

    async def test_error_returns_none(self) -> None:
        mock_fn = AsyncMock(side_effect=RuntimeError("LLM down"))
        reformulator = LLMQueryReformulator(completion_fn=mock_fn)
        result = await reformulator.reformulate("query", ())
        assert result is None

    async def test_memory_error_propagates(self) -> None:
        mock_fn = AsyncMock(side_effect=MemoryError("OOM"))
        reformulator = LLMQueryReformulator(completion_fn=mock_fn)
        with pytest.raises(MemoryError):
            await reformulator.reformulate("query", ())

    async def test_recursion_error_propagates(self) -> None:
        mock_fn = AsyncMock(side_effect=RecursionError)
        reformulator = LLMQueryReformulator(completion_fn=mock_fn)
        with pytest.raises(RecursionError):
            await reformulator.reformulate("query", ())


# ── LLMSufficiencyChecker ────────────────────────────────────────


@pytest.mark.unit
class TestLLMSufficiencyChecker:
    async def test_sufficient_returns_true(self) -> None:
        checker = LLMSufficiencyChecker(
            completion_fn=_mock_completion("SUFFICIENT"),
        )
        entries = (_make_entry(content="relevant answer"),)
        result = await checker.check_sufficiency("query", entries)
        assert result is True

    async def test_insufficient_returns_false(self) -> None:
        checker = LLMSufficiencyChecker(
            completion_fn=_mock_completion("INSUFFICIENT"),
        )
        result = await checker.check_sufficiency("query", ())
        assert result is False

    async def test_ambiguous_response_defaults_to_false(self) -> None:
        checker = LLMSufficiencyChecker(
            completion_fn=_mock_completion("maybe"),
        )
        result = await checker.check_sufficiency("query", ())
        assert result is False

    async def test_error_defaults_to_true(self) -> None:
        """On error, assume sufficient to avoid infinite loops."""
        mock_fn = AsyncMock(side_effect=RuntimeError("LLM down"))
        checker = LLMSufficiencyChecker(completion_fn=mock_fn)
        result = await checker.check_sufficiency("query", ())
        assert result is True

    async def test_empty_entries_checked(self) -> None:
        mock_fn = _mock_completion("INSUFFICIENT")
        checker = LLMSufficiencyChecker(completion_fn=mock_fn)
        result = await checker.check_sufficiency("query", ())
        assert result is False
        mock_fn.assert_awaited_once()

    async def test_memory_error_propagates(self) -> None:
        mock_fn = AsyncMock(side_effect=MemoryError("OOM"))
        checker = LLMSufficiencyChecker(completion_fn=mock_fn)
        with pytest.raises(MemoryError):
            await checker.check_sufficiency("query", ())

    async def test_recursion_error_propagates(self) -> None:
        mock_fn = AsyncMock(side_effect=RecursionError)
        checker = LLMSufficiencyChecker(completion_fn=mock_fn)
        with pytest.raises(RecursionError):
            await checker.check_sufficiency("query", ())
