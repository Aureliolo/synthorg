"""Tests for AgentCardBuilder safe-subset projection."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.a2a.agent_card import AgentCardBuilder, _identity_to_skills
from synthorg.a2a.models import A2AAuthSchemeInfo
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from synthorg.core.role import Skill


def _skill(skill_id: str) -> Skill:
    """Build a minimal Skill for tests."""
    return Skill(id=skill_id, name=skill_id)


def _make_identity(
    *,
    name: str = "test-agent",
    role: str = "developer",
    department: str = "engineering",
    primary_skills: tuple[Skill, ...] = (
        Skill(id="python", name="python"),
        Skill(id="testing", name="testing"),
    ),
    secondary_skills: tuple[Skill, ...] = (Skill(id="sql", name="sql"),),
) -> AgentIdentity:
    """Create a minimal AgentIdentity for testing."""
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role=role,
        department=department,
        model=ModelConfig(
            provider="test-provider",
            model_id="test-capable-001",
        ),
        personality=PersonalityConfig(
            traits=("detail-oriented",),
            communication_style="formal",
        ),
        skills=SkillSet(
            primary=primary_skills,
            secondary=secondary_skills,
        ),
        hiring_date=date(2026, 1, 1),
    )


class TestIdentityToSkills:
    """Skill extraction from AgentIdentity."""

    @pytest.mark.unit
    def test_primary_and_secondary_mapped(self) -> None:
        """Both primary and secondary skills are extracted."""
        identity = _make_identity()
        skills = _identity_to_skills(identity)
        assert len(skills) == 3
        names = {s.name for s in skills}
        assert names == {"python", "testing", "sql"}

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("tag", "expected_count"),
        [("primary", 2), ("secondary", 1)],
    )
    def test_skills_tagged(self, tag: str, expected_count: int) -> None:
        """Skills carry the correct tag."""
        identity = _make_identity()
        skills = _identity_to_skills(identity)
        matching = [s for s in skills if tag in s.tags]
        assert len(matching) == expected_count

    @pytest.mark.unit
    def test_empty_skills(self) -> None:
        """Agent with no skills produces empty tuple."""
        identity = _make_identity(
            primary_skills=(),
            secondary_skills=(),
        )
        skills = _identity_to_skills(identity)
        assert skills == ()


class TestAgentCardBuilder:
    """AgentCardBuilder safe-subset projection."""

    @pytest.mark.unit
    def test_build_includes_safe_fields(self) -> None:
        """Card includes name, role, department, skills."""
        builder = AgentCardBuilder()
        identity = _make_identity()
        card = builder.build(identity, "https://example.com/a2a")

        assert card.name == "test-agent"
        assert "developer" in card.description
        assert "engineering" in card.description
        assert card.url == "https://example.com/a2a"
        assert len(card.skills) == 3

    @pytest.mark.unit
    def test_build_excludes_sensitive_fields(self) -> None:
        """Card does NOT contain personality, model, memory, etc."""
        builder = AgentCardBuilder()
        identity = _make_identity()
        card = builder.build(identity, "https://example.com/a2a")
        card_data = card.model_dump()

        # Sensitive field keys must not appear at the top level
        forbidden_keys = {
            "personality",
            "model",
            "model_config",
            "memory",
            "authority",
            "budget",
            "hiring_date",
            "level",
            "autonomy_level",
            "strategic_output_mode",
            "tools",
        }
        assert not forbidden_keys & set(card_data.keys())

        # Also verify sample sensitive values are absent from full dump
        card_json = str(card_data)
        assert "detail-oriented" not in card_json
        assert "formal" not in card_json
        assert "test-provider" not in card_json
        assert "test-capable-001" not in card_json

    @pytest.mark.unit
    def test_build_with_auth_schemes(self) -> None:
        """Builder passes through configured auth schemes."""
        auth = (A2AAuthSchemeInfo(scheme="api_key"),)
        builder = AgentCardBuilder(default_auth_schemes=auth)
        identity = _make_identity()
        card = builder.build(identity, "https://example.com/a2a")

        assert len(card.auth_schemes) == 1
        assert card.auth_schemes[0].scheme == "api_key"

    @pytest.mark.unit
    def test_build_company_card(self) -> None:
        """Company card aggregates skills from all agents."""
        builder = AgentCardBuilder()
        agents = [
            _make_identity(
                name="agent-a",
                primary_skills=(_skill("python"),),
                secondary_skills=(),
            ),
            _make_identity(
                name="agent-b",
                primary_skills=(_skill("go"),),
                secondary_skills=(_skill("docker"),),
            ),
        ]
        card = builder.build_company_card(
            agents,
            "https://example.com/a2a",
            "Test Corp",
        )

        assert card.name == "Test Corp"
        assert "2 agents" in card.description
        assert card.provider is not None
        assert card.provider.organization == "Test Corp"
        # 1 from agent-a + 2 from agent-b = 3 total
        assert len(card.skills) == 3

    @pytest.mark.unit
    def test_company_card_empty_agents(self) -> None:
        """Company card with no agents has no skills."""
        builder = AgentCardBuilder()
        card = builder.build_company_card(
            [],
            "https://example.com/a2a",
            "Empty Corp",
        )
        assert card.skills == ()
        assert "0 agents" in card.description

    @pytest.mark.unit
    def test_company_card_dedup_across_agents(self) -> None:
        """Same skill ID across different agents is deduplicated."""
        builder = AgentCardBuilder()
        agent_a = _make_identity(
            name="agent-a",
            primary_skills=(_skill("python"),),
            secondary_skills=(),
        )
        agent_b = _make_identity(
            name="agent-b",
            primary_skills=(_skill("python"),),
            secondary_skills=(),
        )
        card = builder.build_company_card(
            [agent_a, agent_b],
            "https://example.com/a2a",
            "Test Corp",
        )
        # Both agents have "python" -> id-based dedup -> 1
        assert len(card.skills) == 1
        assert card.skills[0].id == "python"

    @pytest.mark.unit
    def test_skill_ids_passed_through(self) -> None:
        """Skill IDs come from the internal Skill.id, not fabricated slugs."""
        identity = _make_identity(
            primary_skills=(_skill("python"),),
            secondary_skills=(_skill("docker"),),
        )
        skills = _identity_to_skills(identity)
        assert skills[0].id == "python"
        assert skills[1].id == "docker"

    @pytest.mark.unit
    def test_skill_description_passed_through(self) -> None:
        """Description is rendered verbatim, not stubbed."""
        identity = _make_identity(
            primary_skills=(
                Skill(
                    id="python",
                    name="Python",
                    description="Backend Python 3.14+",
                ),
            ),
            secondary_skills=(),
        )
        skills = _identity_to_skills(identity)
        assert skills[0].description == "Backend Python 3.14+"

    @pytest.mark.unit
    def test_skill_tags_passed_through_with_tier_marker(self) -> None:
        """User tags are preserved; tier marker is appended."""
        identity = _make_identity(
            primary_skills=(
                Skill(id="python", name="Python", tags=("backend", "async")),
            ),
            secondary_skills=(Skill(id="docker", name="Docker", tags=("ops",)),),
        )
        skills = _identity_to_skills(identity)
        primary_skill = next(s for s in skills if s.id == "python")
        secondary_skill = next(s for s in skills if s.id == "docker")
        assert primary_skill.tags == ("backend", "async", "primary")
        assert secondary_skill.tags == ("ops", "secondary")

    @pytest.mark.unit
    def test_skill_input_output_modes_passed_through(self) -> None:
        """input_modes and output_modes propagate without modification."""
        identity = _make_identity(
            primary_skills=(
                Skill(
                    id="python",
                    name="Python",
                    input_modes=("application/json", "text/plain"),
                    output_modes=("application/json",),
                ),
            ),
            secondary_skills=(),
        )
        skills = _identity_to_skills(identity)
        assert skills[0].input_modes == ("application/json", "text/plain")
        assert skills[0].output_modes == ("application/json",)
