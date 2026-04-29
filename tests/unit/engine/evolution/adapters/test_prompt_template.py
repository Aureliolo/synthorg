"""Tests for PromptTemplateAdapter."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.adapters.prompt_template import PromptTemplateAdapter
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.memory.models import MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend


@pytest.mark.unit
class TestPromptTemplateAdapter:
    """Tests for PromptTemplateAdapter."""

    @pytest.fixture
    def mock_memory_backend(self) -> AsyncMock:
        """Create a mock MemoryBackend spec'd against the Protocol."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="memory-id-001")
        return backend

    @pytest.fixture
    def adapter(self, mock_memory_backend: AsyncMock) -> PromptTemplateAdapter:
        """Create a PromptTemplateAdapter with the mock backend."""
        return PromptTemplateAdapter(memory_backend=mock_memory_backend)

    @pytest.mark.asyncio
    async def test_axis_property(self, adapter: PromptTemplateAdapter) -> None:
        """Test that the axis property returns PROMPT_TEMPLATE."""
        assert adapter.axis == AdaptationAxis.PROMPT_TEMPLATE

    @pytest.mark.asyncio
    async def test_name_property(self, adapter: PromptTemplateAdapter) -> None:
        """Test that the name property is non-blank."""
        assert len(adapter.name) > 0
        assert adapter.name == "PromptTemplateAdapter"

    @pytest.mark.asyncio
    async def test_apply_success(
        self,
        adapter: PromptTemplateAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test successful prompt template adaptation."""
        agent_id: NotBlankStr = "agent-001"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.PROMPT_TEMPLATE,
            description="Inject structured reasoning into system prompt",
            changes={
                "prompt_slot": "reasoning",
                "instruction": "Always break problems into sub-components",
            },
            confidence=0.87,
            source=AdaptationSource.SUCCESS,
        )

        await adapter.apply(proposal, agent_id)

        mock_memory_backend.store.assert_called_once()

        call_args = mock_memory_backend.store.call_args
        assert call_args[0][0] == agent_id

        request = call_args[0][1]
        assert isinstance(request, MemoryStoreRequest)
        assert request.category == MemoryCategory.PROCEDURAL
        assert "evolution-prompt-injection" in request.metadata.tags
        assert len(request.content) > 0

    @pytest.mark.asyncio
    async def test_apply_with_complex_changes(
        self,
        adapter: PromptTemplateAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test prompt template with complex changes dict."""
        agent_id: NotBlankStr = "agent-002"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.PROMPT_TEMPLATE,
            description="Add decision tree injection",
            changes={
                "prompt_slot": "decision_logic",
                "tree": {
                    "root": "assess_risk",
                    "branches": ["high", "medium", "low"],
                },
            },
            confidence=0.85,
            source=AdaptationSource.INFLECTION,
        )

        await adapter.apply(proposal, agent_id)

        request = mock_memory_backend.store.call_args[0][1]
        assert request.category == MemoryCategory.PROCEDURAL
        assert "evolution-prompt-injection" in request.metadata.tags

    @pytest.mark.asyncio
    async def test_apply_empty_changes(
        self,
        adapter: PromptTemplateAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test prompt template with empty changes."""
        agent_id: NotBlankStr = "agent-003"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.PROMPT_TEMPLATE,
            description="Log prompt baseline",
            changes={},
            confidence=0.5,
            source=AdaptationSource.SCHEDULED,
        )

        await adapter.apply(proposal, agent_id)

        mock_memory_backend.store.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_store_error(
        self,
        adapter: PromptTemplateAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test when store() raises an exception."""
        agent_id: NotBlankStr = "agent-001"
        mock_memory_backend.store.side_effect = RuntimeError("Store failed")

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.PROMPT_TEMPLATE,
            description="Update prompt",
            changes={"instruction": "Be thorough"},
            confidence=0.9,
            source=AdaptationSource.FAILURE,
        )

        with pytest.raises(RuntimeError, match="Store failed"):
            await adapter.apply(proposal, agent_id)

    @pytest.mark.asyncio
    async def test_apply_tags_include_prompt_injection(
        self,
        adapter: PromptTemplateAdapter,
        mock_memory_backend: AsyncMock,
    ) -> None:
        """Test that memory is tagged with evolution-prompt-injection."""
        agent_id: NotBlankStr = "agent-004"

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.PROMPT_TEMPLATE,
            description="Inject reasoning pattern",
            changes={"pattern": "zero-shot-cot"},
            confidence=0.91,
            source=AdaptationSource.SUCCESS,
        )

        await adapter.apply(proposal, agent_id)

        request = mock_memory_backend.store.call_args[0][1]
        assert "evolution-prompt-injection" in request.metadata.tags
