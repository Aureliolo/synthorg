"""Tests for agent identity and configuration models."""

from datetime import date
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.core.agent import (
    AgentIdentity,
    MemoryConfig,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
    ToolPermissions,
)
from synthorg.core.enums import (
    AgentStatus,
    CollaborationPreference,
    CommunicationVerbosity,
    ConflictApproach,
    CreativityLevel,
    DecisionMakingStyle,
    MemoryLevel,
    RiskTolerance,
    SeniorityLevel,
)
from synthorg.core.role import Authority

from .conftest import (
    AgentIdentityFactory,
    MemoryConfigFactory,
    ModelConfigFactory,
    PersonalityConfigFactory,
    SkillSetFactory,
    ToolPermissionsFactory,
)

pytestmark = pytest.mark.timeout(30)

# ── PersonalityConfig ──────────────────────────────────────────────


@pytest.mark.unit
class TestPersonalityConfig:
    """Tests for PersonalityConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        """Verify default field values for a bare PersonalityConfig."""
        p = PersonalityConfig()
        assert p.traits == ()
        assert p.communication_style == "neutral"
        assert p.risk_tolerance is RiskTolerance.MEDIUM
        assert p.creativity is CreativityLevel.MEDIUM
        assert p.description == ""

    def test_custom_values(self) -> None:
        """Verify explicitly provided values are persisted."""
        p = PersonalityConfig(
            traits=("analytical", "pragmatic"),
            communication_style="concise and technical",
            risk_tolerance=RiskTolerance.LOW,
            creativity=CreativityLevel.HIGH,
            description="A detail-oriented engineer.",
        )
        assert len(p.traits) == 2
        assert p.communication_style == "concise and technical"

    def test_empty_communication_style_rejected(self) -> None:
        """Reject empty string for communication_style."""
        with pytest.raises(ValidationError):
            PersonalityConfig(communication_style="")

    def test_whitespace_communication_style_rejected(self) -> None:
        """Reject whitespace-only communication_style."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            PersonalityConfig(communication_style="   ")

    def test_empty_trait_rejected(self) -> None:
        """Reject empty string in traits tuple."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            PersonalityConfig(traits=("analytical", ""))

    def test_whitespace_trait_rejected(self) -> None:
        """Reject whitespace-only trait entry."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            PersonalityConfig(traits=("  ",))

    def test_frozen(self) -> None:
        """Ensure PersonalityConfig is immutable."""
        p = PersonalityConfig()
        with pytest.raises(ValidationError):
            p.creativity = CreativityLevel.LOW  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid PersonalityConfig."""
        p = PersonalityConfigFactory.build()
        assert isinstance(p, PersonalityConfig)

    def test_big_five_defaults(self) -> None:
        """All Big Five dimensions default to 0.5."""
        p = PersonalityConfig()
        assert p.openness == 0.5
        assert p.conscientiousness == 0.5
        assert p.extraversion == 0.5
        assert p.agreeableness == 0.5
        assert p.stress_response == 0.5

    @pytest.mark.parametrize(
        "dimension",
        [
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "stress_response",
        ],
    )
    def test_big_five_boundaries_accepted(self, dimension: str) -> None:
        """0.0 and 1.0 are accepted for Big Five dimensions."""
        p_low = PersonalityConfig(**{dimension: 0.0})  # type: ignore[arg-type]
        assert getattr(p_low, dimension) == 0.0
        p_high = PersonalityConfig(**{dimension: 1.0})  # type: ignore[arg-type]
        assert getattr(p_high, dimension) == 1.0

    @pytest.mark.parametrize(
        "dimension",
        [
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "stress_response",
        ],
    )
    def test_big_five_below_zero_rejected(self, dimension: str) -> None:
        """Values below 0.0 are rejected for Big Five dimensions."""
        with pytest.raises(ValidationError):
            PersonalityConfig(**{dimension: -0.1})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "dimension",
        [
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "stress_response",
        ],
    )
    def test_big_five_above_one_rejected(self, dimension: str) -> None:
        """Values above 1.0 are rejected for Big Five dimensions."""
        with pytest.raises(ValidationError):
            PersonalityConfig(**{dimension: 1.1})  # type: ignore[arg-type]

    def test_extended_enum_defaults(self) -> None:
        """Extended behavioral enums default to neutral values."""
        p = PersonalityConfig()
        assert p.decision_making is DecisionMakingStyle.CONSULTATIVE
        assert p.collaboration is CollaborationPreference.TEAM
        assert p.verbosity is CommunicationVerbosity.BALANCED
        assert p.conflict_approach is ConflictApproach.COLLABORATE

    def test_extended_enums_custom(self) -> None:
        """Extended behavioral enums accept custom values."""
        p = PersonalityConfig(
            decision_making=DecisionMakingStyle.ANALYTICAL,
            collaboration=CollaborationPreference.INDEPENDENT,
            verbosity=CommunicationVerbosity.TERSE,
            conflict_approach=ConflictApproach.COMPETE,
        )
        assert p.decision_making is DecisionMakingStyle.ANALYTICAL
        assert p.collaboration is CollaborationPreference.INDEPENDENT
        assert p.verbosity is CommunicationVerbosity.TERSE
        assert p.conflict_approach is ConflictApproach.COMPETE

    @pytest.mark.parametrize(
        "dimension",
        [
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "stress_response",
        ],
    )
    def test_big_five_nan_rejected(self, dimension: str) -> None:
        """NaN values are rejected for Big Five dimensions."""
        with pytest.raises(ValidationError):
            PersonalityConfig(**{dimension: float("nan")})  # type: ignore[arg-type]

    def test_description_max_length_rejected(self) -> None:
        """Reject description exceeding max_length."""
        with pytest.raises(ValidationError):
            PersonalityConfig(description="x" * 501)

    def test_communication_style_max_length_rejected(self) -> None:
        """Reject communication_style exceeding max_length."""
        with pytest.raises(ValidationError):
            PersonalityConfig(communication_style="x" * 101)

    def test_backward_compatible_construction(self) -> None:
        """Construction without new fields works identically to before."""
        p = PersonalityConfig(
            traits=("analytical",),
            communication_style="concise",
            risk_tolerance=RiskTolerance.LOW,
            creativity=CreativityLevel.HIGH,
            description="test",
        )
        assert p.openness == 0.5
        assert p.decision_making is DecisionMakingStyle.CONSULTATIVE


# ── SkillSet ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkillSet:
    """Tests for SkillSet defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        """Verify default empty tuples for primary and secondary."""
        s = SkillSet()
        assert s.primary == ()
        assert s.secondary == ()

    def test_custom_values(self) -> None:
        """Verify explicitly provided skill tuples are persisted."""
        s = SkillSet(
            primary=("python", "fastapi"),
            secondary=("docker", "redis"),
        )
        assert "python" in s.primary
        assert "docker" in s.secondary

    def test_empty_skill_name_rejected(self) -> None:
        """Reject empty string in primary skills."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            SkillSet(primary=("python", ""))

    def test_whitespace_skill_name_rejected(self) -> None:
        """Reject whitespace-only skill name in secondary."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            SkillSet(secondary=("  ",))

    def test_empty_primary_error_mentions_primary(self) -> None:
        """Ensure error message references 'primary' for that field."""
        with pytest.raises(ValidationError, match="primary"):
            SkillSet(primary=("python", ""))

    def test_empty_secondary_error_mentions_secondary(self) -> None:
        """Ensure error message references 'secondary' for that field."""
        with pytest.raises(ValidationError, match="secondary"):
            SkillSet(secondary=("  ",))

    def test_frozen(self) -> None:
        """Ensure SkillSet is immutable."""
        s = SkillSet()
        with pytest.raises(ValidationError):
            s.primary = ("new",)  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid SkillSet."""
        s = SkillSetFactory.build()
        assert isinstance(s, SkillSet)


# ── ModelConfig ────────────────────────────────────────────────────


@pytest.mark.unit
class TestModelConfig:
    """Tests for ModelConfig validation, boundaries, and immutability."""

    def test_valid_config(self, sample_model_config: ModelConfig) -> None:
        """Verify fixture-provided ModelConfig fields are correct."""
        assert sample_model_config.provider == "test-provider"
        assert sample_model_config.model_id == "test-model-medium-001"
        assert sample_model_config.temperature == 0.3
        assert sample_model_config.max_tokens == 8192

    def test_defaults(self) -> None:
        """Verify default temperature, max_tokens, and fallback_model."""
        m = ModelConfig(provider="test", model_id="test-model")
        assert m.temperature == 0.7
        assert m.max_tokens == 4096
        assert m.fallback_model is None

    def test_empty_provider_rejected(self) -> None:
        """Reject empty provider string."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="", model_id="test")

    def test_empty_model_id_rejected(self) -> None:
        """Reject empty model_id string."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="")

    def test_empty_fallback_model_rejected(self) -> None:
        """Reject empty fallback_model string."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="m", fallback_model="")

    def test_temperature_below_zero_rejected(self) -> None:
        """Reject temperature below 0.0."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="m", temperature=-0.1)

    def test_temperature_above_two_rejected(self) -> None:
        """Reject temperature above 2.0."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="m", temperature=2.1)

    def test_temperature_boundary_zero(self) -> None:
        """Accept temperature at lower boundary (0.0)."""
        m = ModelConfig(provider="test", model_id="m", temperature=0.0)
        assert m.temperature == 0.0

    def test_temperature_boundary_two(self) -> None:
        """Accept temperature at upper boundary (2.0)."""
        m = ModelConfig(provider="test", model_id="m", temperature=2.0)
        assert m.temperature == 2.0

    def test_max_tokens_zero_rejected(self) -> None:
        """Reject max_tokens of zero."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="m", max_tokens=0)

    def test_max_tokens_negative_rejected(self) -> None:
        """Reject negative max_tokens."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="m", max_tokens=-1)

    def test_whitespace_provider_rejected(self) -> None:
        """Reject whitespace-only provider string."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            ModelConfig(provider="   ", model_id="test")

    def test_whitespace_model_id_rejected(self) -> None:
        """Reject whitespace-only model_id string."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            ModelConfig(provider="test", model_id="   ")

    def test_whitespace_fallback_model_rejected(self) -> None:
        """Reject whitespace-only fallback_model string."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            ModelConfig(provider="test", model_id="m", fallback_model="   ")

    def test_frozen(self, sample_model_config: ModelConfig) -> None:
        """Ensure ModelConfig is immutable."""
        with pytest.raises(ValidationError):
            sample_model_config.temperature = 1.0  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid ModelConfig with sane bounds."""
        m = ModelConfigFactory.build()
        assert isinstance(m, ModelConfig)
        assert 0.0 <= m.temperature <= 2.0


# ── MemoryConfig ───────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryConfig:
    """Tests for MemoryConfig defaults, type constraints, and immutability."""

    def test_defaults(self) -> None:
        """Verify default type is SESSION with no retention."""
        m = MemoryConfig()
        assert m.type is MemoryLevel.SESSION
        assert m.retention_days is None

    def test_custom_values(self) -> None:
        """Verify explicitly provided type and retention_days."""
        m = MemoryConfig(type=MemoryLevel.PERSISTENT, retention_days=30)
        assert m.type is MemoryLevel.PERSISTENT
        assert m.retention_days == 30

    def test_retention_days_zero_rejected(self) -> None:
        """Reject retention_days of zero."""
        with pytest.raises(ValidationError):
            MemoryConfig(retention_days=0)

    def test_retention_days_negative_rejected(self) -> None:
        """Reject negative retention_days."""
        with pytest.raises(ValidationError):
            MemoryConfig(retention_days=-1)

    def test_none_type_with_retention_rejected(self) -> None:
        """Reject retention_days when memory type is NONE."""
        with pytest.raises(ValidationError, match="retention_days must be None"):
            MemoryConfig(type=MemoryLevel.NONE, retention_days=30)

    def test_none_type_without_retention_accepted(self) -> None:
        """Accept NONE memory type when retention_days is omitted."""
        m = MemoryConfig(type=MemoryLevel.NONE)
        assert m.retention_days is None

    def test_frozen(self) -> None:
        """Ensure MemoryConfig is immutable."""
        m = MemoryConfig()
        with pytest.raises(ValidationError):
            m.type = MemoryLevel.PERSISTENT  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid MemoryConfig."""
        m = MemoryConfigFactory.build()
        assert isinstance(m, MemoryConfig)


# ── ToolPermissions ────────────────────────────────────────────────


@pytest.mark.unit
class TestToolPermissions:
    """Tests for ToolPermissions overlap detection, validation, and immutability."""

    def test_defaults(self) -> None:
        """Verify default empty allowed and denied tuples."""
        t = ToolPermissions()
        assert t.allowed == ()
        assert t.denied == ()

    def test_custom_values(self) -> None:
        """Verify non-overlapping allowed and denied are accepted."""
        t = ToolPermissions(
            allowed=("file_system", "git"),
            denied=("deployment",),
        )
        assert "file_system" in t.allowed
        assert "deployment" in t.denied

    def test_overlap_rejected(self) -> None:
        """Reject tools appearing in both allowed and denied."""
        with pytest.raises(ValidationError, match="both allowed and denied"):
            ToolPermissions(
                allowed=("git", "file_system"),
                denied=("git",),
            )

    def test_multiple_overlapping_tools_all_reported(self) -> None:
        """Ensure all overlapping tool names appear in the error."""
        with pytest.raises(ValidationError) as exc_info:
            ToolPermissions(
                allowed=("git", "deploy", "shell"),
                denied=("git", "deploy"),
            )
        error_text = str(exc_info.value)
        assert "deploy" in error_text
        assert "git" in error_text

    def test_case_insensitive_overlap_rejected(self) -> None:
        """Reject case-insensitive overlap between allowed and denied."""
        with pytest.raises(ValidationError, match="both allowed and denied"):
            ToolPermissions(
                allowed=("Git",),
                denied=("git",),
            )

    def test_empty_tool_name_rejected(self) -> None:
        """Reject empty string in allowed tools."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            ToolPermissions(allowed=("git", ""))

    def test_whitespace_tool_name_rejected(self) -> None:
        """Reject whitespace-only tool name in denied."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            ToolPermissions(denied=("  ",))

    def test_frozen(self) -> None:
        """Ensure ToolPermissions is immutable."""
        t = ToolPermissions()
        with pytest.raises(ValidationError):
            t.allowed = ("new",)  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid ToolPermissions."""
        t = ToolPermissionsFactory.build()
        assert isinstance(t, ToolPermissions)


# ── AgentIdentity ──────────────────────────────────────────────────


@pytest.mark.unit
class TestAgentIdentity:
    """Tests for AgentIdentity construction, validation, and serialization."""

    def test_valid_agent(self, sample_agent: AgentIdentity) -> None:
        """Verify fixture-provided agent has expected field values."""
        assert sample_agent.name == "Sarah Chen"
        assert sample_agent.role == "Senior Backend Developer"
        assert sample_agent.department == "Engineering"
        assert sample_agent.level is SeniorityLevel.SENIOR
        assert isinstance(sample_agent.id, UUID)

    def test_auto_generated_id(self, sample_model_config: ModelConfig) -> None:
        """Verify UUID is auto-generated when not provided."""
        agent = AgentIdentity(
            name="Test Agent",
            role="Developer",
            department="Engineering",
            model=sample_model_config,
            hiring_date=date(2026, 1, 1),
        )
        assert isinstance(agent.id, UUID)

    def test_defaults(self, sample_model_config: ModelConfig) -> None:
        """Verify default level, status, and nested config objects."""
        agent = AgentIdentity(
            name="Test",
            role="Dev",
            department="Eng",
            model=sample_model_config,
            hiring_date=date(2026, 1, 1),
        )
        assert agent.level is SeniorityLevel.MID
        assert agent.status is AgentStatus.ACTIVE
        assert isinstance(agent.personality, PersonalityConfig)
        assert isinstance(agent.skills, SkillSet)
        assert isinstance(agent.memory, MemoryConfig)
        assert isinstance(agent.tools, ToolPermissions)
        assert isinstance(agent.authority, Authority)

    def test_model_is_required(self) -> None:
        """Reject construction without the required model field."""
        with pytest.raises(ValidationError):
            AgentIdentity(
                name="Test",
                role="Dev",
                department="Eng",
                hiring_date=date(2026, 1, 1),
            )  # type: ignore[call-arg]

    def test_hiring_date_is_required(self, sample_model_config: ModelConfig) -> None:
        """Reject construction without the required hiring_date field."""
        with pytest.raises(ValidationError):
            AgentIdentity(
                name="Test",
                role="Dev",
                department="Eng",
                model=sample_model_config,
            )  # type: ignore[call-arg]

    def test_empty_name_rejected(self, sample_model_config: ModelConfig) -> None:
        """Reject empty name string."""
        with pytest.raises(ValidationError):
            AgentIdentity(
                name="",
                role="Dev",
                department="Eng",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_empty_role_rejected(self, sample_model_config: ModelConfig) -> None:
        """Reject empty role string."""
        with pytest.raises(ValidationError):
            AgentIdentity(
                name="Test",
                role="",
                department="Eng",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_empty_department_rejected(self, sample_model_config: ModelConfig) -> None:
        """Reject empty department string."""
        with pytest.raises(ValidationError):
            AgentIdentity(
                name="Test",
                role="Dev",
                department="",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_whitespace_name_rejected(self, sample_model_config: ModelConfig) -> None:
        """Reject whitespace-only name string."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            AgentIdentity(
                name="   ",
                role="Dev",
                department="Eng",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_whitespace_role_rejected(self, sample_model_config: ModelConfig) -> None:
        """Reject whitespace-only role string."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            AgentIdentity(
                name="Test",
                role="   ",
                department="Eng",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_whitespace_department_rejected(
        self, sample_model_config: ModelConfig
    ) -> None:
        """Reject whitespace-only department string."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            AgentIdentity(
                name="Test",
                role="Dev",
                department="   ",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_frozen(self, sample_agent: AgentIdentity) -> None:
        """Ensure AgentIdentity is immutable."""
        with pytest.raises(ValidationError):
            sample_agent.name = "Changed"  # type: ignore[misc]

    def test_model_copy_update(self, sample_agent: AgentIdentity) -> None:
        """Verify model_copy creates a new instance without mutating the original."""
        updated = sample_agent.model_copy(
            update={"status": AgentStatus.TERMINATED},
        )
        assert updated.status is AgentStatus.TERMINATED
        assert sample_agent.status is AgentStatus.ACTIVE

    def test_json_roundtrip(self, sample_agent: AgentIdentity) -> None:
        """Verify JSON serialization and deserialization preserves fields."""
        json_str = sample_agent.model_dump_json()
        restored = AgentIdentity.model_validate_json(json_str)
        assert restored.name == sample_agent.name
        assert restored.id == sample_agent.id
        assert restored.model.provider == sample_agent.model.provider

    def test_json_roundtrip_with_full_nested_data(
        self, sample_model_config: ModelConfig
    ) -> None:
        """Verify roundtrip with all nested configs explicitly set."""
        agent = AgentIdentity(
            name="Full Agent",
            role="Lead Dev",
            department="Engineering",
            level=SeniorityLevel.LEAD,
            personality=PersonalityConfig(
                traits=("analytical", "pragmatic"),
                communication_style="direct",
                risk_tolerance=RiskTolerance.HIGH,
            ),
            skills=SkillSet(
                primary=("python", "architecture"),
                secondary=("docker",),
            ),
            model=sample_model_config,
            memory=MemoryConfig(type=MemoryLevel.PERSISTENT, retention_days=90),
            tools=ToolPermissions(allowed=("git",), denied=("deploy",)),
            authority=Authority(
                can_approve=("code_review",),
                reports_to="cto",
                can_delegate_to=("junior_dev",),
                budget_limit=50.0,
            ),
            hiring_date=date(2026, 1, 15),
            status=AgentStatus.ACTIVE,
        )
        json_str = agent.model_dump_json()
        restored = AgentIdentity.model_validate_json(json_str)
        assert restored == agent

    def test_factory(self) -> None:
        """Verify factory produces a valid AgentIdentity with UUID and model."""
        agent = AgentIdentityFactory.build()
        assert isinstance(agent, AgentIdentity)
        assert isinstance(agent.id, UUID)
        assert isinstance(agent.model, ModelConfig)
