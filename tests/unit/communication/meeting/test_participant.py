"""Tests for participant resolver."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from synthorg.communication.meeting.errors import NoParticipantsResolvedError
from synthorg.communication.meeting.participant import (
    RegistryParticipantResolver,
)


def _make_identity(
    agent_id: str | None = None,
    name: str = "agent",
    department: str = "engineering",
) -> MagicMock:
    """Create a mock AgentIdentity."""
    identity = MagicMock()
    identity.id = agent_id or str(uuid4())
    identity.name = name
    identity.department = department
    return identity


@pytest.mark.unit
class TestRegistryParticipantResolver:
    """Tests for RegistryParticipantResolver."""

    @pytest.fixture
    def registry(self) -> MagicMock:
        reg = MagicMock()
        reg.list_active = AsyncMock(return_value=())
        reg.list_by_department = AsyncMock(return_value=())
        reg.get_by_name = AsyncMock(return_value=None)
        return reg

    @pytest.fixture
    def resolver(self, registry: MagicMock) -> RegistryParticipantResolver:
        return RegistryParticipantResolver(registry)

    async def test_resolve_all(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        agents = (_make_identity("id-1"), _make_identity("id-2"))
        registry.list_active.return_value = agents

        result = await resolver.resolve(("all",))

        assert result == ("id-1", "id-2")
        registry.list_active.assert_awaited_once()

    async def test_resolve_department(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        agents = (_make_identity("eng-1"),)
        registry.list_by_department.return_value = agents

        result = await resolver.resolve(("engineering",))

        assert result == ("eng-1",)
        registry.list_by_department.assert_awaited_once_with("engineering")

    async def test_resolve_agent_name(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        agent = _make_identity("alice-id", name="Alice")
        registry.get_by_name.return_value = agent

        result = await resolver.resolve(("Alice",))

        assert result == ("alice-id",)
        registry.get_by_name.assert_awaited_once_with("Alice")

    async def test_context_takes_priority(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        """Context values should be used before registry lookups."""
        result = await resolver.resolve(
            ("author",),
            context={"author": "ctx-agent-id"},
        )

        assert result == ("ctx-agent-id",)
        # Registry should not be called for context-resolved entries.
        registry.list_by_department.assert_not_awaited()
        registry.get_by_name.assert_not_awaited()

    async def test_context_list_value(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        """Context can provide a list of agent IDs."""
        result = await resolver.resolve(
            ("reviewers",),
            context={"reviewers": ["r-1", "r-2"]},
        )

        assert result == ("r-1", "r-2")

    async def test_context_tuple_value(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        """Context can provide a tuple of agent IDs."""
        result = await resolver.resolve(
            ("reviewers",),
            context={"reviewers": ("r-1", "r-2")},
        )

        assert result == ("r-1", "r-2")

    async def test_pass_through_literal_id(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        """Unresolvable entries pass through as literal IDs."""
        literal_id = str(uuid4())

        result = await resolver.resolve((literal_id,))

        assert result == (literal_id,)

    async def test_deduplicates_results(
        self,
        resolver: RegistryParticipantResolver,
        registry: MagicMock,
    ) -> None:
        """Duplicate IDs should be removed."""
        agent = _make_identity("dup-id")
        registry.list_by_department.return_value = (agent,)
        registry.get_by_name.return_value = agent

        # Both "engineering" and "Alice" resolve to the same ID.
        result = await resolver.resolve(("engineering", "Alice"))

        assert result == ("dup-id",)

    async def test_empty_result_raises(
        self,
        resolver: RegistryParticipantResolver,
    ) -> None:
        """Empty participant refs raise NoParticipantsResolvedError."""
        with pytest.raises(NoParticipantsResolvedError):
            await resolver.resolve(())
