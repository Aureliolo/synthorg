"""Tests for TwoTierCompressionStrategy."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.compressor import ExperienceCompressor
from synthorg.memory.consolidation.config import ExperienceCompressorConfig
from synthorg.memory.consolidation.models import (
    CompressedExperience,
    ConsolidationResult,
)
from synthorg.memory.consolidation.two_tier_strategy import (
    TwoTierCompressionStrategy,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata


def _make_detailed_entry(
    entry_id: str = "det-1",
) -> MemoryEntry:
    content = json.dumps(
        {
            "prompt": "implement auth",
            "output": "implemented JWT",
            "verification_feedback": "tests pass",
            "reasoning_trace": ["step 1", "step 2"],
        }
    )
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content=content,
        category=MemoryCategory.EPISODIC,
        metadata=MemoryMetadata(tags=("detailed_experience",)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_non_detailed_entry(entry_id: str = "other-1") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        content="regular memory",
        category=MemoryCategory.SEMANTIC,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_compressed() -> CompressedExperience:
    return CompressedExperience(
        id="comp-1",
        agent_id="agent-1",
        strategic_decisions=("Use JWT for auth",),
        applicable_contexts=("Web API auth",),
        source_artifact_ids=("det-1",),
        compression_ratio=0.3,
        compressor_version="llm-v1",
        metadata=MemoryMetadata(tags=("compressed_experience",)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _mock_compressor(
    compressed: CompressedExperience | None = None,
) -> AsyncMock:
    compressor = AsyncMock()
    compressor.compress = AsyncMock(
        return_value=compressed or _make_compressed(),
    )
    return compressor


def _mock_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.retrieve = AsyncMock(return_value=())
    _counter = iter(range(1, 100))
    backend.store = AsyncMock(
        side_effect=lambda *_a, **_kw: f"stored-{next(_counter)}",
    )
    return backend


class TestTwoTierCompressionStrategy:
    """Tests for TwoTierCompressionStrategy."""

    @pytest.mark.unit
    async def test_compresses_detailed_entries(self) -> None:
        backend = _mock_backend()
        compressor = _mock_compressor()
        config = ExperienceCompressorConfig(enabled=True)
        strategy = TwoTierCompressionStrategy(
            backend=backend,
            compressor=compressor,
            config=config,
        )
        entries = (_make_detailed_entry("det-1"),)
        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert isinstance(result, ConsolidationResult)
        assert len(result.removed_ids) == 1
        assert result.removed_ids[0] == "det-1"
        assert len(result.summary_ids) == 1
        compressor.compress.assert_awaited_once()
        backend.store.assert_awaited_once()

    @pytest.mark.unit
    async def test_ignores_non_detailed_entries(self) -> None:
        backend = _mock_backend()
        compressor = _mock_compressor()
        config = ExperienceCompressorConfig(enabled=True)
        strategy = TwoTierCompressionStrategy(
            backend=backend,
            compressor=compressor,
            config=config,
        )
        entries = (_make_non_detailed_entry(),)
        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert result.removed_ids == ()
        assert result.summary_ids == ()
        compressor.compress.assert_not_awaited()

    @pytest.mark.unit
    async def test_mixed_entries(self) -> None:
        backend = _mock_backend()
        compressor = _mock_compressor()
        config = ExperienceCompressorConfig(enabled=True)
        strategy = TwoTierCompressionStrategy(
            backend=backend,
            compressor=compressor,
            config=config,
        )
        entries = (
            _make_detailed_entry("det-1"),
            _make_non_detailed_entry("other-1"),
            _make_detailed_entry("det-2"),
        )
        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert len(result.removed_ids) == 2
        assert len(result.summary_ids) == 2

    @pytest.mark.unit
    async def test_compressor_error_isolates_per_entry(self) -> None:
        backend = _mock_backend()
        compressor = AsyncMock(spec=ExperienceCompressor)
        compressor.compress = AsyncMock(
            side_effect=RuntimeError("LLM error"),
        )
        config = ExperienceCompressorConfig(enabled=True)
        strategy = TwoTierCompressionStrategy(
            backend=backend,
            compressor=compressor,
            config=config,
        )
        entries = (_make_detailed_entry("det-1"),)
        result = await strategy.consolidate(entries, agent_id="agent-1")
        # Error is isolated -- no entries removed on failure
        assert result.removed_ids == ()
        assert result.summary_ids == ()

    @pytest.mark.unit
    async def test_empty_entries(self) -> None:
        backend = _mock_backend()
        compressor = _mock_compressor()
        config = ExperienceCompressorConfig(enabled=True)
        strategy = TwoTierCompressionStrategy(
            backend=backend,
            compressor=compressor,
            config=config,
        )
        result = await strategy.consolidate((), agent_id="agent-1")
        assert result == ConsolidationResult()

    @pytest.mark.unit
    async def test_stores_compressed_with_correct_tags(self) -> None:
        backend = _mock_backend()
        compressor = _mock_compressor()
        config = ExperienceCompressorConfig(enabled=True)
        strategy = TwoTierCompressionStrategy(
            backend=backend,
            compressor=compressor,
            config=config,
        )
        entries = (_make_detailed_entry("det-1"),)
        await strategy.consolidate(entries, agent_id="agent-1")
        store_call = backend.store.call_args
        request = store_call[0][1]
        assert "compressed_experience" in request.metadata.tags
