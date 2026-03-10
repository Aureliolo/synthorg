"""Tests for AgentRegistryService."""

import pytest

from ai_company.core.enums import AgentStatus
from ai_company.hr.errors import AgentAlreadyRegisteredError, AgentNotFoundError
from ai_company.hr.registry import AgentRegistryService  # noqa: TC001
from tests.unit.hr.conftest import make_agent_identity


@pytest.mark.unit
class TestAgentRegistryService:
    """AgentRegistryService registration and lookup."""

    async def test_register_and_get(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        result = await registry.get(str(identity.id))
        assert result is not None
        assert result.name == "alice"

    async def test_register_duplicate_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        with pytest.raises(AgentAlreadyRegisteredError, match="already registered"):
            await registry.register(identity)

    async def test_unregister(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice")
        await registry.register(identity)
        removed = await registry.unregister(str(identity.id))
        assert removed.name == "alice"
        assert await registry.get(str(identity.id)) is None

    async def test_unregister_not_found_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        with pytest.raises(AgentNotFoundError, match="not found"):
            await registry.unregister("nonexistent")

    async def test_get_nonexistent_returns_none(
        self,
        registry: AgentRegistryService,
    ) -> None:
        result = await registry.get("nonexistent")
        assert result is None

    async def test_get_by_name(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="Bob")
        await registry.register(identity)
        result = await registry.get_by_name("bob")  # case-insensitive
        assert result is not None
        assert result.name == "Bob"

    async def test_get_by_name_not_found(
        self,
        registry: AgentRegistryService,
    ) -> None:
        result = await registry.get_by_name("nobody")
        assert result is None

    async def test_list_active_filters_status(
        self,
        registry: AgentRegistryService,
    ) -> None:
        active = make_agent_identity(name="active-agent", status=AgentStatus.ACTIVE)
        onboarding = make_agent_identity(
            name="onboarding-agent",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(active)
        await registry.register(onboarding)
        result = await registry.list_active()
        assert len(result) == 1
        assert result[0].name == "active-agent"

    async def test_list_active_empty(
        self,
        registry: AgentRegistryService,
    ) -> None:
        result = await registry.list_active()
        assert result == ()

    async def test_list_by_department(
        self,
        registry: AgentRegistryService,
    ) -> None:
        eng = make_agent_identity(name="eng-agent", department="engineering")
        design = make_agent_identity(name="design-agent", department="design")
        await registry.register(eng)
        await registry.register(design)
        result = await registry.list_by_department("engineering")
        assert len(result) == 1
        assert result[0].name == "eng-agent"

    async def test_list_by_department_case_insensitive(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="agent", department="Engineering")
        await registry.register(identity)
        result = await registry.list_by_department("ENGINEERING")
        assert len(result) == 1

    async def test_update_status(
        self,
        registry: AgentRegistryService,
    ) -> None:
        identity = make_agent_identity(name="alice", status=AgentStatus.ACTIVE)
        await registry.register(identity)
        updated = await registry.update_status(
            str(identity.id),
            AgentStatus.ON_LEAVE,
        )
        assert updated.status == AgentStatus.ON_LEAVE
        # Verify stored value is also updated.
        fetched = await registry.get(str(identity.id))
        assert fetched is not None
        assert fetched.status == AgentStatus.ON_LEAVE

    async def test_update_status_not_found_raises(
        self,
        registry: AgentRegistryService,
    ) -> None:
        with pytest.raises(AgentNotFoundError, match="not found"):
            await registry.update_status("nonexistent", AgentStatus.TERMINATED)

    async def test_agent_count_empty(
        self,
        registry: AgentRegistryService,
    ) -> None:
        assert registry.agent_count == 0

    async def test_agent_count_tracks_registrations(
        self,
        registry: AgentRegistryService,
    ) -> None:
        a = make_agent_identity(name="alice")
        b = make_agent_identity(name="bob")
        await registry.register(a)
        assert registry.agent_count == 1
        await registry.register(b)
        assert registry.agent_count == 2
        await registry.unregister(str(a.id))
        assert registry.agent_count == 1
