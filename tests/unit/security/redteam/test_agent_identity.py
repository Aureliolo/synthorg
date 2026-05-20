"""Unit tests for the built-in red-team :class:`AgentIdentity` factory."""

import pytest

from synthorg.core.agent import ModelConfig
from synthorg.core.enums import DepartmentName, SeniorityLevel
from synthorg.core.role_catalog import (
    BUILTIN_ROLES,
    RED_TEAM_ROLE_NAME,
    get_builtin_role,
)
from synthorg.security.redteam.agent import (
    RED_TEAM_AGENT_NAME,
    build_red_team_agent_identity,
)
from tests._shared import FakeClock


@pytest.fixture
def model() -> ModelConfig:
    return ModelConfig(
        provider="example-provider",
        model_id="example-medium-001",
    )


@pytest.mark.unit
class TestRoleCatalogEntry:
    """The Red Team role is registered in BUILTIN_ROLES."""

    def test_role_resolves_by_name(self) -> None:
        role = get_builtin_role(RED_TEAM_ROLE_NAME)
        assert role is not None
        assert role.name == "Red Team"
        assert role.department is DepartmentName.QUALITY_ASSURANCE

    def test_role_appears_in_catalog_tuple(self) -> None:
        names = {r.name for r in BUILTIN_ROLES}
        assert RED_TEAM_ROLE_NAME in names

    def test_role_lookup_case_insensitive(self) -> None:
        assert get_builtin_role("red team") is not None
        assert get_builtin_role("RED TEAM") is not None


@pytest.mark.unit
class TestBuildRedTeamAgentIdentity:
    """Factory produces a valid AgentIdentity."""

    def test_uses_catalog_role_name(self, model: ModelConfig) -> None:
        identity = build_red_team_agent_identity(model=model)
        assert identity.role == RED_TEAM_ROLE_NAME

    def test_default_display_name(self, model: ModelConfig) -> None:
        identity = build_red_team_agent_identity(model=model)
        assert identity.name == RED_TEAM_AGENT_NAME

    def test_custom_display_name(self, model: ModelConfig) -> None:
        identity = build_red_team_agent_identity(model=model, name="Custom Red Team")
        assert identity.name == "Custom Red Team"

    def test_department_is_quality_assurance(self, model: ModelConfig) -> None:
        identity = build_red_team_agent_identity(model=model)
        assert identity.department == DepartmentName.QUALITY_ASSURANCE.value

    def test_seniority_is_senior(self, model: ModelConfig) -> None:
        identity = build_red_team_agent_identity(model=model)
        assert identity.level is SeniorityLevel.SENIOR

    def test_primary_skills_set(self, model: ModelConfig) -> None:
        identity = build_red_team_agent_identity(model=model)
        skill_ids = {s.id for s in identity.skills.primary}
        assert "adversarial-analysis" in skill_ids
        assert "claim-grounding" in skill_ids
        assert "security-review" in skill_ids
        assert "requirements-verification" in skill_ids

    def test_fake_clock_drives_hiring_date(self, model: ModelConfig) -> None:
        clock = FakeClock()
        identity = build_red_team_agent_identity(model=model, clock=clock)
        assert identity.hiring_date == clock.now().date()
