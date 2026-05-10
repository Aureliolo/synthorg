"""Tests for hybrid memory pruning strategy (TTL + Pareto)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.memory.procedural.pruning.hybrid_strategy import (
    HybridPruningStrategy,
)
from synthorg.memory.procedural.pruning.pareto_strategy import (
    ParetoPruningStrategy,
)
from synthorg.memory.procedural.pruning.protocol import PruningStrategy
from synthorg.memory.procedural.pruning.ttl_strategy import TtlPruningStrategy


class TestHybridPruningStrategy:
    """Hybrid pruning strategy tests."""

    @pytest.mark.unit
    async def test_name_property(self) -> None:
        """Test that strategy has correct name."""
        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=100)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)
        assert strategy.name == "hybrid"

    @pytest.mark.unit
    async def test_empty_entries(self) -> None:
        """Test pruning with empty entries."""
        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=100)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)
        result = await strategy.prune(agent_id="test-agent-1", entries=())
        assert result == ()

    @pytest.mark.unit
    async def test_ttl_removes_expired_first(self) -> None:
        """Test that TTL removes old entries before Pareto."""
        now = datetime.now(UTC)

        # Recent entry
        recent = MagicMock()
        recent.id = "mem-recent"
        recent.created_at = now - timedelta(days=10)
        recent.relevance_score = 0.5

        # Expired entry
        expired = MagicMock()
        expired.id = "mem-expired"
        expired.created_at = now - timedelta(days=120)
        expired.relevance_score = 0.9

        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=100)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)

        result = await strategy.prune(
            agent_id="test-agent-1",
            entries=(recent, expired),
        )
        # Expired entry should be removed by TTL
        assert "mem-expired" in result

    @pytest.mark.unit
    async def test_pareto_applied_after_ttl(self) -> None:
        """Test that Pareto is applied to remaining entries after TTL."""
        now = datetime.now(UTC)
        entries = []

        # Create 5 recent entries with varying relevance
        for i in range(5):
            entry = MagicMock()
            entry.id = f"mem-recent-{i}"
            entry.created_at = now - timedelta(days=10)
            entry.relevance_score = 0.5 - (i * 0.05)
            entries.append(entry)

        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=3)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)

        result = await strategy.prune(
            agent_id="test-agent-1",
            entries=tuple(entries),
        )
        # Some entries should be pruned by Pareto after TTL
        assert len(result) >= 1

    @pytest.mark.unit
    async def test_combined_effect_ttl_and_pareto(self) -> None:
        """Test combined effect of TTL and Pareto strategies."""
        now = datetime.now(UTC)
        entries = []

        # Add some expired entries (will be removed by TTL)
        for i in range(2):
            entry = MagicMock()
            entry.id = f"mem-expired-{i}"
            entry.created_at = now - timedelta(days=100 + i)
            entry.relevance_score = 1.0  # high relevance but too old
            entries.append(entry)

        # Add many recent entries (will be filtered by Pareto)
        for i in range(10):
            entry = MagicMock()
            entry.id = f"mem-recent-{i}"
            entry.created_at = now - timedelta(days=10)
            entry.relevance_score = 0.1 * i
            entries.append(entry)

        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=5)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)

        result = await strategy.prune(
            agent_id="test-agent-1",
            entries=tuple(entries),
        )

        # Expired entries should be removed by TTL
        assert "mem-expired-0" in result
        assert "mem-expired-1" in result

    @pytest.mark.unit
    async def test_no_duplicates_in_result(self) -> None:
        """Test that result contains no duplicate IDs."""
        now = datetime.now(UTC)
        entries = []

        for i in range(6):
            entry = MagicMock()
            entry.id = f"mem-{i}"
            entry.created_at = now - timedelta(days=10 + i)
            entry.relevance_score = 0.5
            entries.append(entry)

        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=3)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)

        result = await strategy.prune(
            agent_id="test-agent-1",
            entries=tuple(entries),
        )

        # Check for duplicates
        assert len(result) == len(set(result))

    @pytest.mark.unit
    async def test_injected_ttl_and_pareto_strategies(self) -> None:
        """Test with custom injected strategies."""
        mock_ttl = AsyncMock(spec=PruningStrategy)
        mock_ttl.name = "mock-ttl"
        mock_ttl.prune = AsyncMock(return_value=("mem-expired",))

        mock_pareto = AsyncMock(spec=PruningStrategy)
        mock_pareto.name = "mock-pareto"
        mock_pareto.prune = AsyncMock(return_value=("mem-low-relevance",))

        strategy = HybridPruningStrategy(
            ttl_strategy=mock_ttl,
            pareto_strategy=mock_pareto,
        )

        entry1 = MagicMock()
        entry1.id = "mem-expired"

        entry2 = MagicMock()
        entry2.id = "mem-low-relevance"

        await strategy.prune(
            agent_id="test-agent-1",
            entries=(entry1, entry2),
        )

        # Both TTL and Pareto should be called
        mock_ttl.prune.assert_called_once()
        mock_pareto.prune.assert_called_once()

    @pytest.mark.unit
    async def test_single_entry_no_pruning(self) -> None:
        """Test with single entry under limits."""
        now = datetime.now(UTC)
        entry = MagicMock()
        entry.id = "mem-1"
        entry.created_at = now - timedelta(days=10)
        entry.relevance_score = 0.5

        ttl = TtlPruningStrategy(max_age_days=90)
        pareto = ParetoPruningStrategy(max_entries=100)
        strategy = HybridPruningStrategy(ttl_strategy=ttl, pareto_strategy=pareto)

        result = await strategy.prune(
            agent_id="test-agent-1",
            entries=(entry,),
        )
        assert result == ()
