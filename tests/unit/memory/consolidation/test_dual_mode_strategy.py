"""Tests for DualModeConsolidationStrategy."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.density import DensityClassifier
from synthorg.memory.consolidation.extractive import ExtractivePreserver
from synthorg.memory.consolidation.models import ArchivalMode
from synthorg.memory.consolidation.ops import DensityRoutingOp
from synthorg.memory.consolidation.selectors import HighestRelevanceSelector
from synthorg.memory.consolidation.strategy import ConsolidationStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from tests._shared import mock_of

_NOW = datetime.now(UTC)
_AGENT_ID = "test-agent"


def _make_entry(
    entry_id: str,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    content: str = "Some conversational content about the project.",
    relevance: float | None = 0.5,
    age_hours: int = 0,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT_ID,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(hours=age_hours),
        relevance_score=relevance,
    )


def _make_sparse_entry(
    entry_id: str,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    relevance: float | None = 0.5,
) -> MemoryEntry:
    return _make_entry(
        entry_id,
        category=category,
        content=(
            "We discussed the project timeline and agreed on next steps. "
            "The team decided to prioritize the authentication module."
        ),
        relevance=relevance,
    )


def _make_dense_entry(
    entry_id: str,
    category: MemoryCategory = MemoryCategory.PROCEDURAL,
    relevance: float | None = 0.5,
) -> MemoryEntry:
    return _make_entry(
        entry_id,
        category=category,
        content=(
            "def calculate_total(items):\n"
            "    total = sum(item.price for item in items)\n"
            "    return total * 1.08\n"
            "config = {'host': '192.168.1.100', 'port': 5432}\n"
        ),
        relevance=relevance,
    )


def _make_strategy(
    backend: MemoryBackend | None = None,
    classifier: DensityClassifier | None = None,
    summarizer: AbstractiveSummarizer | None = None,
    extractor: ExtractivePreserver | None = None,
    group_threshold: int = 3,
) -> CompositeConsolidationStrategy:
    if backend is None:
        backend = mock_of[MemoryBackend](
            store=AsyncMock(return_value="summary-1"),
            delete=AsyncMock(return_value=True),
        )
    if classifier is None:
        classifier = DensityClassifier()
    if summarizer is None:
        summarizer = mock_of[AbstractiveSummarizer](
            summarize=AsyncMock(return_value="LLM summary text."),
        )
    if extractor is None:
        extractor = mock_of[ExtractivePreserver](
            extract=MagicMock(
                return_value="[Extractive preservation]\nKey facts: id-1",
            ),
        )
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=group_threshold),
        op=DensityRoutingOp(
            backend=backend,
            classifier=classifier,
            extractor=extractor,
            summarizer=summarizer,
        ),
    )


@pytest.mark.unit
class TestDualModeStrategyProtocol:
    """DualModeConsolidationStrategy satisfies ConsolidationStrategy."""

    def test_isinstance_check(self) -> None:
        strategy = _make_strategy()
        assert isinstance(strategy, ConsolidationStrategy)


@pytest.mark.unit
class TestDualModeStrategyBasic:
    """DualModeConsolidationStrategy basic behaviour."""

    async def test_empty_input(self) -> None:
        strategy = _make_strategy()
        result = await strategy.consolidate((), agent_id=_AGENT_ID)
        assert result.consolidated_count == 0
        assert result.removed_ids == ()
        assert result.summary_id is None
        assert result.mode_assignments == ()

    async def test_below_threshold_skipped(self) -> None:
        strategy = _make_strategy(group_threshold=5)
        entries = tuple(_make_sparse_entry(f"m{i}") for i in range(2))
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert result.consolidated_count == 0

    async def test_group_threshold_validation(self) -> None:
        with pytest.raises(ValueError, match="group_threshold"):
            _make_strategy(group_threshold=1)


@pytest.mark.unit
class TestDualModeStrategySparse:
    """DualModeConsolidationStrategy with sparse content."""

    async def test_sparse_group_uses_abstractive_mode(self) -> None:
        backend = AsyncMock()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        summarizer = AsyncMock()
        summarizer.summarize = AsyncMock(return_value="Concise summary.")

        strategy = _make_strategy(
            backend=backend,
            summarizer=summarizer,
        )
        entries = tuple(
            _make_sparse_entry(f"m{i}", relevance=0.1 * i) for i in range(4)
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)

        assert result.consolidated_count == 3
        assert result.summary_id is not None

        # All removed entries should have ABSTRACTIVE mode
        modes = {a.original_id: a.mode for a in result.mode_assignments}
        for removed_id in result.removed_ids:
            assert modes[removed_id] == ArchivalMode.ABSTRACTIVE

    async def test_sparse_calls_summarizer(self) -> None:
        summarizer = AsyncMock()
        summarizer.summarize = AsyncMock(return_value="Summary.")

        strategy = _make_strategy(summarizer=summarizer)
        entries = tuple(
            _make_sparse_entry(f"m{i}", relevance=0.1 * i) for i in range(3)
        )
        await strategy.consolidate(entries, agent_id=_AGENT_ID)

        # Summarizer called for 2 removed entries (best one kept)
        assert summarizer.summarize.call_count == 2


@pytest.mark.unit
class TestDualModeStrategyDense:
    """DualModeConsolidationStrategy with dense content."""

    async def test_dense_group_uses_extractive_mode(self) -> None:
        backend = AsyncMock()
        backend.store = AsyncMock(return_value="extract-1")
        backend.delete = AsyncMock(return_value=True)

        extractor = MagicMock()
        extractor.extract = MagicMock(return_value="[Extractive preservation]\nfacts")

        strategy = _make_strategy(
            backend=backend,
            extractor=extractor,
        )
        entries = tuple(_make_dense_entry(f"d{i}", relevance=0.1 * i) for i in range(4))
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)

        assert result.consolidated_count == 3

        # All removed entries should have EXTRACTIVE mode
        modes = {a.original_id: a.mode for a in result.mode_assignments}
        for removed_id in result.removed_ids:
            assert modes[removed_id] == ArchivalMode.EXTRACTIVE

    async def test_dense_calls_extractor(self) -> None:
        extractor = MagicMock()
        extractor.extract = MagicMock(return_value="Extracted.")

        strategy = _make_strategy(extractor=extractor)
        entries = tuple(_make_dense_entry(f"d{i}", relevance=0.1 * i) for i in range(3))
        await strategy.consolidate(entries, agent_id=_AGENT_ID)

        # Extractor called for 2 removed entries (best one kept)
        assert extractor.extract.call_count == 2


@pytest.mark.unit
class TestDualModeStrategyMultiCategory:
    """DualModeConsolidationStrategy with multiple categories."""

    async def test_multi_category_independent(self) -> None:
        """Different categories are processed independently."""
        backend = AsyncMock()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_strategy(backend=backend)
        entries = (
            _make_sparse_entry("e1", MemoryCategory.EPISODIC, relevance=0.1),
            _make_sparse_entry("e2", MemoryCategory.EPISODIC, relevance=0.5),
            _make_sparse_entry("e3", MemoryCategory.EPISODIC, relevance=0.9),
            _make_dense_entry("p1", MemoryCategory.PROCEDURAL, relevance=0.3),
            _make_dense_entry("p2", MemoryCategory.PROCEDURAL, relevance=0.7),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)

        # Episodic group (3 entries, above threshold) should consolidate
        assert result.consolidated_count == 2
        assert "e3" not in result.removed_ids  # highest relevance kept
        # Procedural group (2 entries, below threshold=3) skipped
        assert "p1" not in result.removed_ids
        assert "p2" not in result.removed_ids


@pytest.mark.unit
class TestDualModeStrategyEntrySelection:
    """DualModeConsolidationStrategy keeps best entry."""

    async def test_keeps_highest_relevance(self) -> None:
        strategy = _make_strategy()
        entries = (
            _make_sparse_entry("low", relevance=0.1),
            _make_sparse_entry("mid", relevance=0.5),
            _make_sparse_entry("high", relevance=0.9),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert "high" not in result.removed_ids
        assert "low" in result.removed_ids
        assert "mid" in result.removed_ids

    async def test_fifty_fifty_defaults_to_abstractive(self) -> None:
        """50/50 dense/sparse split defaults to ABSTRACTIVE."""
        backend = AsyncMock()
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)

        summarizer = AsyncMock()
        summarizer.summarize = AsyncMock(return_value="Summary.")

        strategy = _make_strategy(
            backend=backend,
            summarizer=summarizer,
            group_threshold=4,
        )
        # 2 sparse + 2 dense = 50/50 split
        entries = (
            _make_sparse_entry("s1", relevance=0.1),
            _make_sparse_entry("s2", relevance=0.2),
            _make_dense_entry("d1", MemoryCategory.EPISODIC, relevance=0.3),
            _make_dense_entry("d2", MemoryCategory.EPISODIC, relevance=0.4),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)

        # 50/50 → ABSTRACTIVE (strict > comparison)
        modes = {a.original_id: a.mode for a in result.mode_assignments}
        for mode in modes.values():
            assert mode == ArchivalMode.ABSTRACTIVE

    async def test_none_relevance_treated_as_zero(self) -> None:
        """Entries with None relevance are treated as lowest priority."""
        strategy = _make_strategy()
        entries = (
            _make_sparse_entry("none-rel", relevance=None),
            _make_sparse_entry("low-rel", relevance=0.1),
            _make_sparse_entry("high-rel", relevance=0.9),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        # high-rel (0.9) is kept
        assert "high-rel" not in result.removed_ids
        # none-rel (treated as 0.0) and low-rel (0.1) are removed
        assert "none-rel" in result.removed_ids
        assert "low-rel" in result.removed_ids

    async def test_equal_relevance_keeps_most_recent(self) -> None:
        strategy = _make_strategy()
        entries = (
            _make_entry("old", relevance=0.5, age_hours=10),
            _make_entry("mid", relevance=0.5, age_hours=5),
            _make_entry("new", relevance=0.5, age_hours=1),
        )
        result = await strategy.consolidate(entries, agent_id=_AGENT_ID)
        assert "new" not in result.removed_ids
        assert "old" in result.removed_ids
        assert "mid" in result.removed_ids
