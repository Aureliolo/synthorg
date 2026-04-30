"""Tests for StrategySelectionAdapter."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.adapters.strategy_selection import (
    StrategySelectionAdapter,
)
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.memory.models import MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend


@pytest.mark.unit
class TestStrategySelectionAdapter:
    """Tests for StrategySelectionAdapter."""

    @pytest.fixture
    def mock_memory_backend(self) -> AsyncMock:
        """Create a mock MemoryBackend spec'd against the Protocol."""
        backend = AsyncMock(spec=MemoryBackend)
        # Configure the spec'd ``store`` coroutine in place so the
        # MemoryBackend Protocol constraint is preserved; replacing
        # ``backend.store`` with a bare ``AsyncMock()`` would drop the
        # spec and silently absorb any attribute access.
        backend.store.return_value = "memory-id-001"
        return backend

    @pytest.fixture
    def adapter(self, mock_memory_backend: AsyncMock) -> StrategySelectionAdapter:
        """Create a StrategySelectionAdapter with the mock backend."""
        return StrategySelectionAdapter(memory_backend=mock_memory_backend)

    @pytest.mark.asyncio
    async def test_axis_property(self, adapter: StrategySelectionAdapter) -> None:
        """Test that the axis property returns STRATEGY_SELECTION."""
        assert adapter.axis == AdaptationAxis.STRATEGY_SELECTION

    @pytest.mark.asyncio
    async def test_name_property(self, adapter: StrategySelectionAdapter) -> None:
        """Test that the name property is non-blank."""
        assert len(adapter.name) > 0
        assert adapter.name == "StrategySelectionAdapter"

    @pytest.mark.asyncio
    async def test_apply_success(
        self,
        adapter: StrategySelectionAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test successful strategy selection adaptation."""
        agent_id: NotBlankStr = "agent-001"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.STRATEGY_SELECTION,
            description="Prefer systematic search over rapid iteration",
            changes={
                "strategy": "systematic_search",
                "depth": 5,
            },
            confidence=0.88,
            source=AdaptationSource.SUCCESS,
        )

        await adapter.apply(proposal, agent_id)

        mock_memory_backend.store.assert_called_once()

        call_args = mock_memory_backend.store.call_args
        assert call_args[0][0] == agent_id

        request = call_args[0][1]
        assert isinstance(request, MemoryStoreRequest)
        assert request.category == MemoryCategory.PROCEDURAL
        assert "evolution-strategy" in request.metadata.tags
        assert len(request.content) > 0

    @pytest.mark.asyncio
    async def test_apply_empty_changes(
        self,
        adapter: StrategySelectionAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test strategy selection with empty changes."""
        agent_id: NotBlankStr = "agent-002"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.STRATEGY_SELECTION,
            description="Record baseline strategy",
            changes={},
            confidence=0.5,
            source=AdaptationSource.SCHEDULED,
        )

        await adapter.apply(proposal, agent_id)

        mock_memory_backend.store.assert_called_once()
        request = mock_memory_backend.store.call_args[0][1]
        assert request.category == MemoryCategory.PROCEDURAL

    @pytest.mark.asyncio
    async def test_apply_store_error(
        self,
        adapter: StrategySelectionAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test when store() raises an exception."""
        agent_id: NotBlankStr = "agent-001"
        mock_memory_backend.store.side_effect = RuntimeError("Store failed")

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.STRATEGY_SELECTION,
            description="Update strategy",
            changes={"strategy": "new_approach"},
            confidence=0.9,
            source=AdaptationSource.FAILURE,
        )

        with pytest.raises(RuntimeError, match="Store failed"):
            await adapter.apply(proposal, agent_id)

    @pytest.mark.asyncio
    async def test_apply_tags_include_evolution_strategy(
        self,
        adapter: StrategySelectionAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test that memory is tagged with evolution-strategy."""
        agent_id: NotBlankStr = "agent-003"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.STRATEGY_SELECTION,
            description="Optimize strategy for efficiency",
            changes={"efficiency": 0.95},
            confidence=0.92,
            source=AdaptationSource.SUCCESS,
        )

        await adapter.apply(proposal, agent_id)

        request = mock_memory_backend.store.call_args[0][1]
        assert "evolution-strategy" in request.metadata.tags
