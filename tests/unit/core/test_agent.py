"""Tests for agent identity and configuration models."""

from datetime import date
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.core.agent import (
    AgentIdentity,
    AgentRetentionRule,
    MemoryConfig,
    ModelConfig,
    SkillSet,
    ToolPermissions,
)
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.memory_enums import MemoryCategory, MemoryLevel
from synthorg.core.role import Authority, Skill
from synthorg.hr.enums import AgentStatus

from .conftest import (
    AgentIdentityFactory,
    MemoryConfigFactory,
    ModelConfigFactory,
    SkillSetFactory,
    ToolPermissionsFactory,
)

#: The persona-label fields flatten whitespace before the length check, so a
#: whitespace-only value arrives at that check as the empty string.
_EMPTY_LABEL = "at least 1 item after validation"

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
        """Verify explicitly provided Skill objects are persisted."""
        python = Skill(id="python", name="Python")
        fastapi = Skill(id="fastapi", name="FastAPI")
        docker = Skill(id="docker", name="Docker")
        redis = Skill(id="redis", name="Redis")
        s = SkillSet(primary=(python, fastapi), secondary=(docker, redis))
        assert python in s.primary
        assert docker in s.secondary

    def test_non_skill_entry_rejected(self) -> None:
        """Primary/secondary must contain Skill objects, not strings."""
        with pytest.raises(ValidationError):
            SkillSet(primary=("python",))  # type: ignore[arg-type]

    def test_empty_primary_error_mentions_primary(self) -> None:
        """Ensure validation errors surface the failing field name."""
        with pytest.raises(ValidationError, match="primary"):
            SkillSet(primary=("python",))  # type: ignore[arg-type]

    def test_empty_secondary_error_mentions_secondary(self) -> None:
        """Ensure validation errors surface the failing field name."""
        with pytest.raises(ValidationError, match="secondary"):
            SkillSet(secondary=("docker",))  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        """Ensure SkillSet is immutable."""
        s = SkillSet()
        with pytest.raises(ValidationError):
            s.primary = (Skill(id="new", name="New"),)  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid SkillSet."""
        s = SkillSetFactory.build()
        assert isinstance(s, SkillSet)

    def test_primary_secondary_overlap_rejected(self) -> None:
        """A skill id cannot appear in both primary and secondary tiers."""
        python = Skill(id="python", name="Python")
        with pytest.raises(
            ValidationError, match="Skills cannot appear in both primary and secondary"
        ):
            SkillSet(primary=(python,), secondary=(python,))

    def test_primary_duplicate_ids_rejected(self) -> None:
        """Duplicate skill ids within the primary tier are rejected."""
        python_a = Skill(id="python", name="Python")
        python_b = Skill(id="python", name="Python (senior)", proficiency=0.9)
        with pytest.raises(
            ValidationError, match="Duplicate skill ids in primary tier"
        ):
            SkillSet(primary=(python_a, python_b))

    def test_secondary_duplicate_ids_rejected(self) -> None:
        """Duplicate skill ids within the secondary tier are rejected."""
        go_a = Skill(id="go", name="Go")
        go_b = Skill(id="go", name="Go (systems)")
        with pytest.raises(
            ValidationError, match="Duplicate skill ids in secondary tier"
        ):
            SkillSet(secondary=(go_a, go_b))


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
        """An unset ceiling defers rather than carrying a flat number.

        ``None`` is what distinguishes "the operator chose nothing" from "the
        operator chose a small value", which is what lets
        ``engine.agent_max_response_tokens`` answer for the first without
        overriding the second. A flat number here is nobody's choice and
        every agent's ceiling.

        ``top_p`` and ``reasoning_effort`` defer on the same reasoning, so an
        agent stating no preference inherits the ladder rather than a number
        this class picked for it.
        """
        m = ModelConfig(provider="test", model_id="test-model")
        assert m.temperature == 0.7
        assert m.max_tokens is None
        assert m.top_p is None
        assert m.reasoning_effort is None

    def test_top_p_accepted_within_range(self) -> None:
        """A vendor's published nucleus threshold is bindable per agent.

        Every vendor that publishes a temperature publishes ``top_p`` beside
        it, so an agent able to carry one and not the other can only ever
        apply half a recommendation.
        """
        m = ModelConfig(provider="test", model_id="m", top_p=0.95)
        assert m.top_p == pytest.approx(0.95)

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_top_p_out_of_range_rejected(self, value: float) -> None:
        """Reject a nucleus threshold outside the unit interval."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="m", top_p=value)

    def test_reasoning_effort_accepted(self) -> None:
        """An agent may bind its own reasoning depth."""
        m = ModelConfig(
            provider="test", model_id="m", reasoning_effort=ReasoningEffort.HIGH
        )
        assert m.reasoning_effort is ReasoningEffort.HIGH

    def test_reasoning_effort_rejects_unknown_depth(self) -> None:
        """Reject a depth outside the provider-agnostic vocabulary."""
        with pytest.raises(ValidationError):
            ModelConfig.model_validate(
                {"provider": "test", "model_id": "m", "reasoning_effort": "deepest"}
            )

    def test_an_agent_has_no_spare_model(self) -> None:
        """A bare fallback model id names a model with no connection.

        The same id reached through two connections is two different calls,
        billed and rate-limited separately, so an id with no provider beside
        it is exactly the ambiguity explicit provider binding removes.
        """
        with pytest.raises(ValidationError):
            ModelConfig.model_validate(
                {
                    "provider": "test",
                    "model_id": "test-model",
                    "fallback_model": "test-other-model",
                },
            )

    def test_empty_provider_rejected(self) -> None:
        """Reject empty provider string."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="", model_id="test")

    def test_empty_model_id_rejected(self) -> None:
        """Reject empty model_id string."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="test", model_id="")

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

    @pytest.mark.parametrize("rung", ["basic", "capable", "expert"])
    def test_capability_literal_as_model_id_rejected(self, rung: str) -> None:
        """Reject a bare capability rung used as model_id."""
        with pytest.raises(ValidationError, match="not the capability"):
            ModelConfig(provider="test", model_id=rung)

    def test_model_id_containing_capability_substring_accepted(self) -> None:
        """Accept a concrete model id that merely contains a rung word."""
        m = ModelConfig(provider="test", model_id="test-model-capable-001")
        assert m.model_id == "test-model-capable-001"

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

    def test_retention_overrides_defaults_empty(self) -> None:
        """Default retention_overrides is an empty tuple."""
        m = MemoryConfig()
        assert m.retention_overrides == ()

    def test_retention_overrides_with_rules(self) -> None:
        """Accept valid per-category retention overrides."""
        m = MemoryConfig(
            type=MemoryLevel.PERSISTENT,
            retention_overrides=(
                AgentRetentionRule(
                    category=MemoryCategory.SEMANTIC,
                    retention_days=365,
                ),
                AgentRetentionRule(
                    category=MemoryCategory.EPISODIC,
                    retention_days=180,
                ),
            ),
        )
        assert len(m.retention_overrides) == 2
        assert m.retention_overrides[0].category is MemoryCategory.SEMANTIC
        assert m.retention_overrides[0].retention_days == 365

    def test_retention_overrides_duplicate_categories_rejected(self) -> None:
        """Reject duplicate categories in retention_overrides."""
        with pytest.raises(
            ValidationError,
            match="Duplicate retention override categories",
        ):
            MemoryConfig(
                retention_overrides=(
                    AgentRetentionRule(
                        category=MemoryCategory.WORKING,
                        retention_days=7,
                    ),
                    AgentRetentionRule(
                        category=MemoryCategory.WORKING,
                        retention_days=14,
                    ),
                ),
            )

    def test_retention_overrides_rejected_when_none_type(self) -> None:
        """Reject retention_overrides when memory type is NONE."""
        with pytest.raises(
            ValidationError,
            match="retention_overrides must be empty",
        ):
            MemoryConfig(
                type=MemoryLevel.NONE,
                retention_overrides=(
                    AgentRetentionRule(
                        category=MemoryCategory.SEMANTIC,
                        retention_days=30,
                    ),
                ),
            )

    def test_retention_overrides_coexists_with_retention_days(self) -> None:
        """Both retention_days and retention_overrides can be set."""
        m = MemoryConfig(
            type=MemoryLevel.PERSISTENT,
            retention_days=90,
            retention_overrides=(
                AgentRetentionRule(
                    category=MemoryCategory.SEMANTIC,
                    retention_days=365,
                ),
            ),
        )
        assert m.retention_days == 90
        assert len(m.retention_overrides) == 1


# ── AgentRetentionRule ────────────────────────────────────────────


@pytest.mark.unit
class TestAgentRetentionRule:
    """Tests for AgentRetentionRule model."""

    def test_valid_rule(self) -> None:
        """Accept a valid category and retention_days."""
        rule = AgentRetentionRule(
            category=MemoryCategory.EPISODIC,
            retention_days=30,
        )
        assert rule.category is MemoryCategory.EPISODIC
        assert rule.retention_days == 30

    def test_retention_days_zero_rejected(self) -> None:
        """Reject retention_days of zero."""
        with pytest.raises(ValidationError):
            AgentRetentionRule(
                category=MemoryCategory.WORKING,
                retention_days=0,
            )

    def test_retention_days_negative_rejected(self) -> None:
        """Reject negative retention_days."""
        with pytest.raises(ValidationError):
            AgentRetentionRule(
                category=MemoryCategory.WORKING,
                retention_days=-1,
            )

    def test_frozen(self) -> None:
        """Ensure AgentRetentionRule is immutable."""
        rule = AgentRetentionRule(
            category=MemoryCategory.SEMANTIC,
            retention_days=30,
        )
        with pytest.raises(ValidationError):
            rule.retention_days = 60  # type: ignore[misc]


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

    def test_mcp_capability_with_surrounding_whitespace_accepted(self) -> None:
        """Pin the whitespace path: validation runs on the normalized form."""
        t = ToolPermissions(mcp_capabilities=("  tasks:read  ",))
        assert t.mcp_capabilities == ("  tasks:read  ",)

    def test_mcp_capability_uppercase_accepted_via_casefold(self) -> None:
        """Pin the case path: validation runs on the casefolded form."""
        t = ToolPermissions(mcp_capabilities=("Tasks:Read",))
        assert t.mcp_capabilities == ("Tasks:Read",)

    def test_mcp_capability_invalid_format_rejected(self) -> None:
        """Reject capability that fails the pattern even after normalization."""
        with pytest.raises(ValidationError, match="Invalid MCP capability pattern"):
            ToolPermissions(mcp_capabilities=("  not-a-pattern  ",))

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
        """Verify default status and nested config objects."""
        agent = AgentIdentity(
            name="Test",
            role="Dev",
            department="Eng",
            model=sample_model_config,
            hiring_date=date(2026, 1, 1),
        )
        assert agent.status is AgentStatus.ACTIVE
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
        """A label that is empty once flattened is not a label."""
        with pytest.raises(ValidationError, match=_EMPTY_LABEL):
            AgentIdentity(
                name="   ",
                role="Dev",
                department="Eng",
                model=sample_model_config,
                hiring_date=date(2026, 1, 1),
            )

    def test_whitespace_role_rejected(self, sample_model_config: ModelConfig) -> None:
        """A label that is empty once flattened is not a label."""
        with pytest.raises(ValidationError, match=_EMPTY_LABEL):
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
        """A label that is empty once flattened is not a label."""
        with pytest.raises(ValidationError, match=_EMPTY_LABEL):
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
            skills=SkillSet(
                primary=(
                    Skill(id="python", name="Python"),
                    Skill(id="architecture", name="Architecture"),
                ),
                secondary=(Skill(id="docker", name="Docker"),),
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
