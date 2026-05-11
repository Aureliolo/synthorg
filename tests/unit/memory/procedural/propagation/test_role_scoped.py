"""Tests for role-scoped memory propagation strategy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import MemoryCategory
from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.procedural.propagation.role_scoped import (
    RoleScopedPropagation,
)
from synthorg.memory.protocol import MemoryBackend


class TestRoleScopedPropagation:
    """Role-scoped propagation strategy tests."""

    @pytest.mark.unit
    async def test_name_property(self) -> None:
        """Test that strategy has correct name."""
        strategy = RoleScopedPropagation(max_targets=10)
        assert strategy.name == "role_scoped"

    @pytest.mark.unit
    async def test_propagate_returns_count(self) -> None:
        """Test that propagate returns number of agents propagated to."""
        strategy = RoleScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.role = "engineer"

        target_agent1 = MagicMock()
        target_agent1.id = "agent-2"
        target_agent1.role = "engineer"

        target_agent2 = MagicMock()
        target_agent2.id = "agent-3"
        target_agent2.role = "engineer"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(return_value=(target_agent1, target_agent2))

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        assert result == 2

    @pytest.mark.unit
    async def test_propagate_excludes_source_agent(self) -> None:
        """Test that source agent is not in target list."""
        strategy = RoleScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.role = "engineer"

        target_agent = MagicMock()
        target_agent.id = "agent-2"
        target_agent.role = "engineer"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(
            return_value=(source_agent, target_agent),
        )

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        # Only target_agent should be propagated to
        assert result == 1

    @pytest.mark.unit
    async def test_propagate_filters_by_role(self) -> None:
        """Test that only same-role agents receive the memory."""
        strategy = RoleScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.role = "engineer"

        engineer1 = MagicMock()
        engineer1.id = "agent-2"
        engineer1.role = "engineer"

        manager = MagicMock()
        manager.id = "agent-3"
        manager.role = "manager"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(
            return_value=(engineer1, manager),
        )

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        # Only engineer1 should receive it
        assert result == 1

    @pytest.mark.unit
    async def test_propagate_respects_max_targets(self) -> None:
        """Test that propagation respects max_targets limit."""
        strategy = RoleScopedPropagation(max_targets=2)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.role = "engineer"

        targets = []
        for i in range(5):
            agent = MagicMock(spec=AgentIdentity)
            agent.id = f"agent-{i + 2}"
            agent.role = "engineer"
            targets.append(agent)

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(return_value=tuple(targets))

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        # Should be limited to max_targets
        assert result == 2

    @pytest.mark.unit
    async def test_propagate_adds_propagation_tag(self) -> None:
        """Test that propagated memories get propagation tag."""
        strategy = RoleScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.role = "engineer"

        target_agent = MagicMock()
        target_agent.id = "agent-2"
        target_agent.role = "engineer"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(return_value=(target_agent,))

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        # Verify backend.store was called with propagation tag
        assert backend.store.called
        call_args = backend.store.call_args
        stored_request = call_args[0][1]  # Second positional arg (request)
        tag_values = stored_request.metadata.tags
        assert any("propagated:" in t for t in tag_values)
        assert result == 1

    @pytest.mark.unit
    async def test_no_same_role_agents_returns_zero(self) -> None:
        """Test when no other agents have same role."""
        strategy = RoleScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.role = "engineer"

        different_role_agent = MagicMock()
        different_role_agent.id = "agent-2"
        different_role_agent.role = "designer"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(return_value=(different_role_agent,))

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        assert result == 0

    @pytest.mark.unit
    async def test_default_max_targets_is_10(self) -> None:
        """Test that default max_targets is 10."""
        strategy = RoleScopedPropagation()
        assert strategy.max_targets == 10

    @pytest.mark.unit
    async def test_empty_registry_returns_zero(self) -> None:
        """Test when registry has no active agents."""
        strategy = RoleScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.role = "engineer"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_active = AsyncMock(return_value=())

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        result = await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        assert result == 0
