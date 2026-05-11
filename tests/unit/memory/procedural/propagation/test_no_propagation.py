"""Tests for no-propagation strategy (baseline)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.models import MemoryEntry
from synthorg.memory.procedural.propagation.no_propagation import (
    NoPropagation,
)
from synthorg.memory.protocol import MemoryBackend


class TestNoPropagation:
    """No-propagation strategy tests."""

    @pytest.mark.unit
    async def test_name_property(self) -> None:
        """Test that strategy has correct name."""
        strategy = NoPropagation()
        assert strategy.name == "none"

    @pytest.mark.unit
    async def test_propagate_returns_zero(self) -> None:
        """Test that propagate always returns 0."""
        strategy = NoPropagation()
        memory_entry = MagicMock(spec=MemoryEntry)
        registry = AsyncMock(spec=AgentRegistryService)
        backend = AsyncMock(spec=MemoryBackend)

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        assert result == 0

    @pytest.mark.unit
    async def test_propagate_does_not_call_registry(self) -> None:
        """Test that registry is not called."""
        strategy = NoPropagation()
        memory_entry = MagicMock(spec=MemoryEntry)
        registry = AsyncMock(spec=AgentRegistryService)
        backend = AsyncMock(spec=MemoryBackend)

        await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        registry.assert_not_called()

    @pytest.mark.unit
    async def test_propagate_does_not_call_backend(self) -> None:
        """Test that memory backend is not called."""
        strategy = NoPropagation()
        memory_entry = MagicMock(spec=MemoryEntry)
        registry = AsyncMock(spec=AgentRegistryService)
        backend = AsyncMock(spec=MemoryBackend)

        await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        backend.assert_not_called()

    @pytest.mark.unit
    async def test_multiple_calls_return_zero(self) -> None:
        """Test multiple calls all return 0."""
        strategy = NoPropagation()
        registry = AsyncMock(spec=AgentRegistryService)
        backend = AsyncMock(spec=MemoryBackend)

        for i in range(5):
            entry = MagicMock(spec=MemoryEntry)
            result = await strategy.propagate(
                source_agent_id=f"agent-{i}",
                memory_entry=entry,
                registry=registry,
                memory_backend=backend,
            )
            assert result == 0
