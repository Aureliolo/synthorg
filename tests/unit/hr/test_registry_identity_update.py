"""Tests for AgentRegistryService.apply_identity_update().

The MCP write surface is privileged: it can mutate everything except
the truly-immutable identifiers (id, name, department) and the status
slot (which has its own ``update_status`` path). These tests pin the
blocklist, the model_copy semantics, and the audit/version-snapshot
side effects.
"""

from uuid import uuid4

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import AgentStatus, AutonomyLevel
from synthorg.hr.errors import AgentNotFoundError
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability.events.hr import HR_REGISTRY_IDENTITY_UPDATED
from tests.unit.hr.conftest import make_agent_identity


def _make_identity(
    *,
    name: str = "update-test",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    return make_agent_identity(
        name=name,
        role="test-role",
        department=department,
        level=level,
    )


class TestApplyIdentityUpdate:
    """apply_identity_update() permits broad MCP-driven mutations."""

    @pytest.mark.unit
    async def test_update_role_and_level(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        updated = await registry.apply_identity_update(
            str(identity.id),
            {"role": "principal-engineer", "level": SeniorityLevel.SENIOR},
            saved_by="mcp",
        )
        assert updated.role == "principal-engineer"
        assert updated.level == SeniorityLevel.SENIOR
        assert updated.id == identity.id
        assert updated.name == identity.name

        current = await registry.get(str(identity.id))
        assert current is not None
        assert current.role == "principal-engineer"

    @pytest.mark.unit
    async def test_update_autonomy_level(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        updated = await registry.apply_identity_update(
            str(identity.id),
            {"autonomy_level": AutonomyLevel.SEMI},
            saved_by="mcp",
        )
        assert updated.autonomy_level == AutonomyLevel.SEMI

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "field",
        [
            "id",
            "name",
            "department",
            "status",
        ],
    )
    async def test_blocked_fields_rejected(self, field: str) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        new_value: object = "renamed"
        if field == "id":
            new_value = uuid4()
        elif field == "status":
            new_value = AgentStatus.TERMINATED

        with pytest.raises(ValueError, match=r"immutable|not allowed"):
            await registry.apply_identity_update(
                str(identity.id),
                {field: new_value},
                saved_by="mcp",
            )

    @pytest.mark.unit
    async def test_unknown_agent_raises_not_found(self) -> None:
        registry = AgentRegistryService()
        with pytest.raises(AgentNotFoundError):
            await registry.apply_identity_update(
                str(uuid4()),
                {"role": "engineer"},
                saved_by="mcp",
            )

    @pytest.mark.unit
    async def test_emits_identity_updated_event(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        with structlog.testing.capture_logs() as logs:
            await registry.apply_identity_update(
                str(identity.id),
                {"role": "staff-engineer"},
                saved_by="mcp:caller-1",
            )

        events = [
            e
            for e in logs
            if e.get("event") == HR_REGISTRY_IDENTITY_UPDATED
            and e.get("agent_id") == str(identity.id)
            and e.get("updated_fields") == ["role"]
        ]
        assert events, "expected HR_REGISTRY_IDENTITY_UPDATED with role"

    @pytest.mark.unit
    async def test_empty_updates_is_noop(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        updated = await registry.apply_identity_update(
            str(identity.id),
            {},
            saved_by="mcp",
        )
        # Returned identity equals the registered one (frozen model_copy).
        assert updated.id == identity.id
        assert updated.role == identity.role
        assert updated.level == identity.level

    @pytest.mark.unit
    async def test_returns_new_object_not_mutated(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        updated = await registry.apply_identity_update(
            str(identity.id),
            {"role": "lead"},
            saved_by="mcp",
        )
        # Original is untouched.
        assert identity.role == "test-role"
        assert updated.role == "lead"
        assert updated is not identity
