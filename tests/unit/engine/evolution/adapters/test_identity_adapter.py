"""Tests for IdentityAdapter."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.adapters.identity_adapter import IdentityAdapter
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.engine.identity.store.protocol import IdentityVersionStore
from synthorg.hr.enums import AgentStatus
from synthorg.hr.seniority import SeniorityLevel


@pytest.mark.unit
class TestIdentityAdapter:
    """Tests for IdentityAdapter."""

    @pytest.fixture
    def mock_identity_store(self) -> AsyncMock:
        """Create a mock IdentityVersionStore spec'd against the Protocol."""
        return AsyncMock(spec=IdentityVersionStore)

    @pytest.fixture
    def sample_identity(self) -> AgentIdentity:
        """Create a sample AgentIdentity."""
        return AgentIdentity(
            name="Alice",
            role="Engineer",
            department="Engineering",
            hiring_date=date(2024, 1, 1),
            model=ModelConfig(
                provider="example-provider",
                model_id="example-medium-001",
            ),
            level=SeniorityLevel.MID,
            status=AgentStatus.ACTIVE,
        )

    @pytest.fixture
    def adapter(self, mock_identity_store: AsyncMock) -> IdentityAdapter:
        """Create an IdentityAdapter with the mock store."""
        return IdentityAdapter(identity_store=mock_identity_store)

    @pytest.mark.asyncio
    async def test_axis_property(self, adapter: IdentityAdapter) -> None:
        """Test that the axis property returns IDENTITY."""
        assert adapter.axis == AdaptationAxis.IDENTITY

    @pytest.mark.asyncio
    async def test_name_property(self, adapter: IdentityAdapter) -> None:
        """Test that the name property is non-blank."""
        assert len(adapter.name) > 0
        assert adapter.name == "IdentityAdapter"

    @pytest.mark.asyncio
    async def test_apply_success(
        self,
        adapter: IdentityAdapter,
        mock_identity_store: AsyncMock,
        sample_identity: AgentIdentity,
    ) -> None:
        """Test successful identity adaptation."""
        agent_id: NotBlankStr = "agent-001"
        mock_identity_store.get_current.return_value = sample_identity

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.IDENTITY,
            description="Update personality",
            changes={"personality": {"openness": 0.8}},
            confidence=0.95,
            source=AdaptationSource.SUCCESS,
        )

        await adapter.apply(proposal, agent_id)

        mock_identity_store.get_current.assert_called_once_with(agent_id)
        mock_identity_store.put.assert_called_once()

        call_args = mock_identity_store.put.call_args
        assert call_args[0][0] == agent_id
        assert call_args[1]["saved_by"] == "evolution"

        evolved_identity = call_args[0][1]
        assert isinstance(evolved_identity, AgentIdentity)

    @pytest.mark.asyncio
    async def test_apply_with_empty_changes(
        self,
        adapter: IdentityAdapter,
        mock_identity_store: AsyncMock,
        sample_identity: AgentIdentity,
    ) -> None:
        """Test adaptation with empty changes dict."""
        agent_id: NotBlankStr = "agent-001"
        mock_identity_store.get_current.return_value = sample_identity

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.IDENTITY,
            description="No-op adaptation",
            changes={},
            confidence=0.5,
            source=AdaptationSource.SCHEDULED,
        )

        await adapter.apply(proposal, agent_id)

        mock_identity_store.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_agent_not_found(
        self,
        adapter: IdentityAdapter,
        mock_identity_store: AsyncMock,
    ) -> None:
        """Test when agent is not found in the store."""
        agent_id: NotBlankStr = "nonexistent"
        mock_identity_store.get_current.return_value = None

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.IDENTITY,
            description="Update identity",
            changes={"name": "Bob"},
            confidence=0.9,
            source=AdaptationSource.FAILURE,
        )

        with pytest.raises(ValueError, match="not found"):
            await adapter.apply(proposal, agent_id)

    @pytest.mark.asyncio
    async def test_apply_store_error(
        self,
        adapter: IdentityAdapter,
        mock_identity_store: AsyncMock,
        sample_identity: AgentIdentity,
    ) -> None:
        """Test when store.put() raises an exception."""
        agent_id: NotBlankStr = "agent-001"
        mock_identity_store.get_current.return_value = sample_identity
        mock_identity_store.put.side_effect = RuntimeError("Store error")

        proposal = AdaptationProposal(
            agent_id=agent_id,
            axis=AdaptationAxis.IDENTITY,
            description="Update identity",
            changes={"name": "Bob"},
            confidence=0.9,
            source=AdaptationSource.FAILURE,
        )

        with pytest.raises(RuntimeError, match="Store error"):
            await adapter.apply(proposal, agent_id)
