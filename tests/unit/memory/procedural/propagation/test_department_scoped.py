"""Tests for department-scoped memory propagation strategy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import MemoryCategory
from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.procedural.propagation.department_scoped import (
    DepartmentScopedPropagation,
)
from synthorg.memory.protocol import MemoryBackend


class TestDepartmentScopedPropagation:
    """Department-scoped propagation strategy tests."""

    @pytest.mark.unit
    async def test_name_property(self) -> None:
        """Test that strategy has correct name."""
        strategy = DepartmentScopedPropagation(max_targets=10)
        assert strategy.name == "department_scoped"

    @pytest.mark.unit
    async def test_propagate_returns_count(self) -> None:
        """Test that propagate returns number of agents propagated to."""
        strategy = DepartmentScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        target_agent1 = MagicMock()
        target_agent1.id = "agent-2"
        target_agent1.department = "engineering"

        target_agent2 = MagicMock()
        target_agent2.id = "agent-3"
        target_agent2.department = "engineering"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_by_department = AsyncMock(
            return_value=(target_agent1, target_agent2),
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

        assert result == 2

    @pytest.mark.unit
    async def test_propagate_excludes_source_agent(self) -> None:
        """Test that source agent is not in target list."""
        strategy = DepartmentScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        target_agent = MagicMock()
        target_agent.id = "agent-2"
        target_agent.department = "engineering"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_by_department = AsyncMock(
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
    async def test_propagate_filters_by_department(self) -> None:
        """Test that list_by_department is called with source department."""
        strategy = DepartmentScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_by_department = AsyncMock(return_value=())

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-copy-1")

        memory_entry = MagicMock(spec=MemoryEntry)
        memory_entry.category = MemoryCategory.PROCEDURAL
        memory_entry.namespace = "default"
        memory_entry.content = "learned procedure"
        memory_entry.metadata = MemoryMetadata()
        memory_entry.expires_at = None

        await strategy.propagate(
            source_agent_id="agent-1",
            memory_entry=memory_entry,
            registry=registry,
            memory_backend=backend,
        )

        # Verify list_by_department was called with correct department
        registry.list_by_department.assert_called_once_with("engineering")

    @pytest.mark.unit
    async def test_propagate_respects_max_targets(self) -> None:
        """Test that propagation respects max_targets limit."""
        strategy = DepartmentScopedPropagation(max_targets=2)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        targets = []
        for i in range(5):
            agent = MagicMock(spec=AgentIdentity)
            agent.id = f"agent-{i + 2}"
            agent.department = "engineering"
            targets.append(agent)

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_by_department = AsyncMock(return_value=tuple(targets))

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
    async def test_no_other_agents_in_department_returns_zero(self) -> None:
        """Test when no other agents in same department."""
        strategy = DepartmentScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_by_department = AsyncMock(return_value=())

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
        strategy = DepartmentScopedPropagation()
        assert strategy.max_targets == 10

    @pytest.mark.unit
    async def test_propagate_multiple_departments(self) -> None:
        """Test that agents from different departments are not included."""
        strategy = DepartmentScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        engineering_agent = MagicMock()
        engineering_agent.id = "agent-2"
        engineering_agent.department = "engineering"

        sales_agent = MagicMock()
        sales_agent.id = "agent-3"
        sales_agent.department = "sales"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        # list_by_department should return only engineering agents
        registry.list_by_department = AsyncMock(
            return_value=(engineering_agent,),
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

        # Should only propagate to engineering_agent
        assert result == 1

    @pytest.mark.unit
    async def test_propagate_with_single_target(self) -> None:
        """Test propagation with exactly one other agent."""
        strategy = DepartmentScopedPropagation(max_targets=10)

        source_agent = MagicMock()
        source_agent.id = "agent-1"
        source_agent.department = "engineering"

        target_agent = MagicMock()
        target_agent.id = "agent-2"
        target_agent.department = "engineering"

        registry = AsyncMock(spec=AgentRegistryService)
        registry.get = AsyncMock(return_value=source_agent)
        registry.list_by_department = AsyncMock(
            return_value=(target_agent,),
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

        assert result == 1
