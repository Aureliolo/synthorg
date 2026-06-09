"""Tests for the Simple composite (HighestRelevanceSelector + ConcatenationOp).

Post ADR-0005 axis split: ``SimpleConsolidationStrategy`` is gone;
"simple" is ``Composite(HighestRelevanceSelector, ConcatenationOp)``.
These assertions are unchanged from the pre-split monolith suite --
the composite is byte-identical (see ``test_axis_split_golden``).
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.ops import ConcatenationOp
from synthorg.memory.consolidation.selectors import HighestRelevanceSelector
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from tests._shared import mock_of

_NOW = datetime.now(UTC)
_AGENT_ID = "test-agent"


def _make_entry(
    entry_id: str,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    relevance: float | None = 0.5,
    age_hours: int = 0,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT_ID,
        category=category,
        content=f"Content for {entry_id}",
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(hours=age_hours),
        relevance_score=relevance,
    )


def _make_simple(
    backend: MemoryBackend,
    *,
    group_threshold: int = 3,
) -> CompositeConsolidationStrategy:
    """Build the Simple composite (selector group-threshold validated)."""
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=group_threshold),
        op=ConcatenationOp(backend=backend),
    )


@pytest.mark.unit
class TestSimpleComposite:
    """Simple composite behaviour (byte-identical with the old monolith)."""

    async def test_empty_input(self) -> None:
        backend = mock_of[MemoryBackend]()
        strategy = _make_simple(backend)
        result = await strategy.consolidate((), agent_id=_AGENT_ID)
        assert result.consolidated_count == 0
        assert result.removed_ids == ()
        assert result.summary_id is None

    async def test_single_category_below_threshold(self) -> None:
        backend = mock_of[MemoryBackend]()
        strategy = _make_simple(backend, group_threshold=5)
        entries = tuple(_make_entry(f"m{i}") for i in range(2))
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert result.consolidated_count == 0
        cast(AsyncMock, backend.delete).assert_not_called()

    async def test_single_category_above_threshold(self) -> None:
        backend = mock_of[MemoryBackend]()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_simple(backend, group_threshold=3)
        entries = tuple(_make_entry(f"m{i}", relevance=0.1 * i) for i in range(5))
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert result.consolidated_count == 4
        assert result.summary_id == "summary-1"
        assert len(result.removed_ids) == 4

    async def test_multi_category(self) -> None:
        backend = mock_of[MemoryBackend]()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_simple(backend, group_threshold=3)
        entries = (
            _make_entry("e1", MemoryCategory.EPISODIC, relevance=0.1),
            _make_entry("e2", MemoryCategory.EPISODIC, relevance=0.5),
            _make_entry("e3", MemoryCategory.EPISODIC, relevance=0.9),
            _make_entry("s1", MemoryCategory.SEMANTIC, relevance=0.3),
            _make_entry("s2", MemoryCategory.SEMANTIC, relevance=0.7),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert result.consolidated_count == 2
        assert "e3" not in result.removed_ids
        assert "e1" in result.removed_ids
        assert "e2" in result.removed_ids

    async def test_keeps_highest_relevance(self) -> None:
        backend = mock_of[MemoryBackend]()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_simple(backend, group_threshold=3)
        entries = (
            _make_entry("low", relevance=0.1),
            _make_entry("mid", relevance=0.5),
            _make_entry("high", relevance=0.9),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert "high" not in result.removed_ids
        assert "low" in result.removed_ids
        assert "mid" in result.removed_ids

    async def test_build_summary_truncation(self) -> None:
        backend = mock_of[MemoryBackend]()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_simple(backend, group_threshold=3)
        long_content = "x" * 300
        entries = tuple(_make_entry(f"m{i}", relevance=0.1 * i) for i in range(4))
        long_entries = tuple(
            MemoryEntry(
                id=e.id,
                agent_id=e.agent_id,
                category=e.category,
                content=long_content,
                metadata=e.metadata,
                created_at=e.created_at,
                relevance_score=e.relevance_score,
            )
            for e in entries
        )
        await strategy.consolidate(long_entries, agent_id=_AGENT_ID)

        store_call = cast("AsyncMock", backend.store).call_args
        summary_content = store_call[0][1].content
        assert "..." in summary_content

    async def test_none_relevance_scores(self) -> None:
        backend = mock_of[MemoryBackend]()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_simple(backend, group_threshold=3)
        entries = (
            _make_entry("m0", relevance=None),
            _make_entry("m1", relevance=None),
            _make_entry("m2", relevance=None),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert result.consolidated_count == 2
        assert result.summary_id == "summary-1"

    async def test_equal_relevance_keeps_most_recent(self) -> None:
        """When relevance scores are equal, most recently created wins."""
        backend = mock_of[MemoryBackend]()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_simple(backend, group_threshold=3)
        entries = (
            _make_entry("old", relevance=0.5, age_hours=10),
            _make_entry("mid", relevance=0.5, age_hours=5),
            _make_entry("new", relevance=0.5, age_hours=1),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert "new" not in result.removed_ids
        assert "old" in result.removed_ids
        assert "mid" in result.removed_ids

    def test_group_threshold_validation(self) -> None:
        backend = mock_of[MemoryBackend]()
        with pytest.raises(ValueError, match="group_threshold must be >= 2"):
            _make_simple(backend, group_threshold=1)
        with pytest.raises(ValueError, match="group_threshold must be >= 2"):
            _make_simple(backend, group_threshold=0)
