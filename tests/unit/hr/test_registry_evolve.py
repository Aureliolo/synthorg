"""Tests for AgentRegistryService.evolve_identity()."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.hr.errors import AgentNotFoundError
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel


def _make_identity(
    *,
    agent_id: str | None = None,
    name: str = "evolve-test",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    from uuid import UUID

    return AgentIdentity(
        id=UUID(agent_id) if agent_id else uuid4(),
        name=name,
        role="test-role",
        department=department,
        level=level,
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


class TestEvolveIdentity:
    """evolve_identity() replaces identity with validation."""

    @pytest.mark.unit
    async def test_valid_evolution(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        evolved = identity.model_copy(
            update={"level": SeniorityLevel.SENIOR},
        )
        result = await registry.evolve_identity(
            str(identity.id),
            evolved,
            evolution_rationale="test evolution",
        )
        assert result.level == SeniorityLevel.SENIOR

        # Registry should reflect the change.
        current = await registry.get(str(identity.id))
        assert current is not None
        assert current.level == SeniorityLevel.SENIOR

    @pytest.mark.unit
    async def test_not_found_raises(self) -> None:
        registry = AgentRegistryService()
        identity = _make_identity()

        with pytest.raises(AgentNotFoundError):
            await registry.evolve_identity(
                str(identity.id),
                identity,
                evolution_rationale="test",
            )

    @pytest.mark.unit
    async def test_id_mismatch_raises(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        different_id = _make_identity(name="evolve-test", department="engineering")
        with pytest.raises(ValueError, match="does not match"):
            await registry.evolve_identity(
                str(identity.id),
                different_id,
                evolution_rationale="test",
            )

    @pytest.mark.unit
    async def test_name_change_rejected(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        # Same id but different name -- force with model_copy hack.
        evolved = identity.model_copy(update={"name": "different-name"})
        with pytest.raises(ValueError, match="name cannot be changed"):
            await registry.evolve_identity(
                str(identity.id),
                evolved,
                evolution_rationale="test",
            )

    @pytest.mark.unit
    async def test_department_change_rejected(self) -> None:
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        evolved = identity.model_copy(update={"department": "marketing"})
        with pytest.raises(ValueError, match="department cannot be changed"):
            await registry.evolve_identity(
                str(identity.id),
                evolved,
                evolution_rationale="test",
            )

    @pytest.mark.unit
    async def test_model_change_allowed(self) -> None:
        """Model changes (unlike update_identity) are allowed."""
        identity = _make_identity()
        registry = AgentRegistryService()
        await registry.register(identity)

        new_model = ModelConfig(
            provider="test-provider",
            model_id="test-large-001",
        )
        evolved = identity.model_copy(update={"model": new_model})
        result = await registry.evolve_identity(
            str(identity.id),
            evolved,
            evolution_rationale="model upgrade",
        )
        assert str(result.model.model_id) == "test-large-001"
