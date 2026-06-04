"""Unit tests for system prompt construction."""

from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING

import pytest
import structlog.testing
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import (
    AutonomyLevel,
    CollaborationPreference,
    CommunicationVerbosity,
    ConflictApproach,
    CreativityLevel,
    DecisionMakingStyle,
    RiskTolerance,
    SeniorityLevel,
)
from synthorg.engine.errors import PromptBuildError
from synthorg.engine.prompt import (
    SystemPrompt,
    build_error_prompt,
    build_system_prompt,
)
from synthorg.engine.prompt_template import (
    AUTONOMY_INSTRUCTIONS,
    AUTONOMY_MINIMAL,
    AUTONOMY_SUMMARY,
    PROMPT_TEMPLATE_VERSION,
)
from synthorg.engine.token_estimation import DefaultTokenEstimator
from synthorg.observability.events.prompt import (
    PROMPT_BUILD_START,
    PROMPT_BUILD_SUCCESS,
    PROMPT_BUILD_TOKEN_TRIMMED,
)
from synthorg.providers.models import ChatMessage
from synthorg.security.autonomy.models import EffectiveAutonomy

if TYPE_CHECKING:
    from synthorg.core.company import Company
    from synthorg.core.role import Role
    from synthorg.core.task import Task
    from synthorg.providers.models import ToolDefinition

# ── TestBuildSystemPrompt ────────────────────────────────────────


@pytest.mark.unit
class TestBuildSystemPrompt:
    """Tests for the build_system_prompt() public API."""

    def test_minimal_agent_produces_valid_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Minimal call with only agent produces a prompt with identity."""
        result = build_system_prompt(agent=sample_agent_with_personality)

        assert isinstance(result, SystemPrompt)
        assert sample_agent_with_personality.name in result.content
        assert sample_agent_with_personality.role in result.content
        assert sample_agent_with_personality.department in result.content
        assert result.estimated_tokens > 0
        assert result.content.strip()

    def test_personality_traits_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """All personality dimensions appear in the rendered prompt."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        p = sample_agent_with_personality.personality

        assert p.communication_style in result.content
        assert p.risk_tolerance.value in result.content
        assert p.creativity.value in result.content
        for trait in p.traits:
            assert trait in result.content

    def test_different_personalities_produce_different_prompts(
        self,
    ) -> None:
        """Two agents with different personality configs get different prompts."""
        model_cfg = ModelConfig(provider="test", model_id="test-001")
        hiring = date(2026, 1, 1)

        agent_a = AgentIdentity(
            name="Agent A",
            role="Developer",
            department="Engineering",
            model=model_cfg,
            hiring_date=hiring,
            personality=PersonalityConfig(
                communication_style="verbose and friendly",
                risk_tolerance=RiskTolerance.HIGH,
                creativity=CreativityLevel.HIGH,
            ),
        )
        agent_b = AgentIdentity(
            name="Agent B",
            role="Developer",
            department="Engineering",
            model=model_cfg,
            hiring_date=hiring,
            personality=PersonalityConfig(
                communication_style="terse and formal",
                risk_tolerance=RiskTolerance.LOW,
                creativity=CreativityLevel.LOW,
            ),
        )

        prompt_a = build_system_prompt(agent=agent_a)
        prompt_b = build_system_prompt(agent=agent_b)

        assert prompt_a.content != prompt_b.content
        assert "verbose and friendly" in prompt_a.content
        assert "terse and formal" in prompt_b.content

    def test_role_description_included(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_role_with_description: Role,
    ) -> None:
        """Role description appears in prompt when role is provided."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            role=sample_role_with_description,
        )

        assert sample_role_with_description.description in result.content

    def test_custom_template_overrides_default(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Custom template string is used instead of the default."""
        custom = "Hello, I am {{ agent_name }} working as {{ agent_role }}."
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            custom_template=custom,
        )

        assert result.content == (
            f"Hello, I am {sample_agent_with_personality.name} "
            f"working as {sample_agent_with_personality.role}."
        )

    def test_authority_boundaries_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Authority fields (can_approve, reports_to, etc.) appear in prompt."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        auth = sample_agent_with_personality.authority

        for approval in auth.can_approve:
            assert approval in result.content
        assert auth.reports_to is not None
        assert auth.reports_to in result.content
        for delegate in auth.can_delegate_to:
            assert delegate in result.content
        assert f"{auth.budget_limit:.2f}" in result.content

    def test_company_context_injected(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_company: Company,
    ) -> None:
        """Company name and department names appear when provided."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            company=sample_company,
        )

        assert sample_company.name in result.content
        for dept in sample_company.departments:
            assert dept.name in result.content

    def test_tools_not_in_default_template(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_tool_definitions: tuple[ToolDefinition, ...],
    ) -> None:
        """Tools passed to build_system_prompt don't appear (D22)."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            available_tools=sample_tool_definitions,
        )

        assert "Available Tools" not in result.content
        for tool in sample_tool_definitions:
            assert tool.name not in result.content
        assert "tools" not in result.sections

    def test_tools_render_in_custom_template(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_tool_definitions: tuple[ToolDefinition, ...],
    ) -> None:
        """Custom templates with {% if tools %} still render tools."""
        custom = (
            "Agent: {{ agent_name }}\n"
            "{% if tools %}\n"
            "Tools:\n"
            "{% for tool in tools %}"
            "- {{ tool.name }}: {{ tool.description }}\n"
            "{% endfor %}"
            "{% endif %}"
        )
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            available_tools=sample_tool_definitions,
            custom_template=custom,
        )

        for tool in sample_tool_definitions:
            assert tool.name in result.content
            assert tool.description in result.content

    def test_task_context_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Task title, description, and acceptance criteria appear."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert sample_task_with_criteria.title in result.content
        assert sample_task_with_criteria.description in result.content
        for criterion in sample_task_with_criteria.acceptance_criteria:
            assert criterion.description in result.content

    def test_task_budget_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Task budget appears in prompt when > 0."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert f"{sample_task_with_criteria.budget_limit:.2f}" in result.content

    def test_new_personality_dimensions_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """New personality dimensions (verbosity, decision_making, etc.) appear."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        p = sample_agent_with_personality.personality

        assert p.verbosity.value in result.content
        assert p.decision_making.value in result.content
        assert p.collaboration.value in result.content
        assert p.conflict_approach.value in result.content

    def test_new_personality_dimensions_with_custom_values(self) -> None:
        """Prompt reflects explicitly set personality dimensions."""
        model_cfg = ModelConfig(provider="test", model_id="test-001")
        agent = AgentIdentity(
            name="Custom Agent",
            role="Dev",
            department="Eng",
            model=model_cfg,
            hiring_date=date(2026, 1, 1),
            personality=PersonalityConfig(
                verbosity=CommunicationVerbosity.TERSE,
                decision_making=DecisionMakingStyle.DIRECTIVE,
                collaboration=CollaborationPreference.INDEPENDENT,
                conflict_approach=ConflictApproach.COMPETE,
            ),
        )
        result = build_system_prompt(agent=agent)
        assert "terse" in result.content
        assert "directive" in result.content
        assert "independent" in result.content
        assert "compete" in result.content

    def test_no_task_section_when_task_is_none(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """No 'Current Task' section when task is None."""
        result = build_system_prompt(agent=sample_agent_with_personality)

        assert "Current Task" not in result.content
        assert "task" not in result.sections

    def test_no_tools_section_in_default_template(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Default template never includes 'Available Tools' section (D22)."""
        result = build_system_prompt(agent=sample_agent_with_personality)

        assert "Available Tools" not in result.content
        assert "tools" not in result.sections

    def test_no_company_section_when_company_is_none(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """No 'Company Context' section when company is None."""
        result = build_system_prompt(agent=sample_agent_with_personality)

        assert "Company Context" not in result.content
        assert "company" not in result.sections


# ── TestSeniorityAutonomy ────────────────────────────────────────


@pytest.mark.unit
class TestSeniorityAutonomy:
    """Tests for seniority-based autonomy instructions."""

    @staticmethod
    def _make_agent(
        name: str,
        role: str,
        department: str,
        level: SeniorityLevel,
    ) -> AgentIdentity:
        """Create a minimal agent identity at the given seniority level."""
        return AgentIdentity(
            name=name,
            role=role,
            department=department,
            level=level,
            model=ModelConfig(provider="test", model_id="test-001"),
            hiring_date=date(2026, 1, 1),
        )

    def test_junior_gets_guidance_instructions(self) -> None:
        """Junior agents get step-by-step guidance language."""
        agent = self._make_agent(
            "Junior Dev",
            "Developer",
            "Engineering",
            SeniorityLevel.JUNIOR,
        )
        result = build_system_prompt(agent=agent)

        assert "Follow instructions carefully" in result.content
        assert "seek approval" in result.content.lower()

    def test_senior_gets_ownership_instructions(self) -> None:
        """Senior agents get ownership-focused language."""
        agent = self._make_agent(
            "Senior Dev",
            "Developer",
            "Engineering",
            SeniorityLevel.SENIOR,
        )
        result = build_system_prompt(agent=agent)

        assert "Take ownership" in result.content

    def test_c_suite_gets_strategic_scope(self) -> None:
        """C-suite agents get strategic language."""
        agent = self._make_agent(
            "CEO",
            "Chief Executive",
            "Executive",
            SeniorityLevel.C_SUITE,
        )
        result = build_system_prompt(agent=agent)

        assert "company-wide authority" in result.content.lower()
        assert "vision" in result.content.lower()

    def test_all_levels_produce_unique_instructions(self) -> None:
        """Each seniority level maps to distinct autonomy text."""
        instructions = set(AUTONOMY_INSTRUCTIONS.values())
        assert len(instructions) == len(SeniorityLevel)


# ── TestTokenEstimation ──────────────────────────────────────────


@pytest.mark.unit
class TestTokenEstimation:
    """Tests for token estimation and budget trimming."""

    def test_default_estimator_positive(self) -> None:
        """Non-empty text produces positive token estimate."""
        estimator = DefaultTokenEstimator()
        assert estimator.estimate_tokens("Hello world, this is a test.") > 0

    def test_default_estimator_empty(self) -> None:
        """Empty text produces zero tokens."""
        estimator = DefaultTokenEstimator()
        assert estimator.estimate_tokens("") == 0

    def test_estimated_tokens_populated(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """SystemPrompt.estimated_tokens is set and positive."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        assert result.estimated_tokens > 0

    def test_max_tokens_triggers_trimming(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        sample_company: Company,
    ) -> None:
        """Very low max_tokens causes optional sections to be removed."""
        # First build without limit to know the full size.
        full = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            company=sample_company,
        )
        assert "task" in full.sections
        assert "company" in full.sections

        # Now build with a tight token budget to force trimming.
        trimmed = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            company=sample_company,
            max_tokens=10,
        )

        # All optional sections should be removed.
        assert "company" not in trimmed.sections
        assert "task" not in trimmed.sections
        # Core sections remain.
        assert "identity" in trimmed.sections
        assert "personality" in trimmed.sections

    def test_custom_estimator_used(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Custom token estimator is called during prompt construction."""
        call_count = 0

        class CountingEstimator:
            def estimate_tokens(self, text: str) -> int:
                nonlocal call_count
                call_count += 1
                return len(text) // 4

            def estimate_conversation_tokens(
                self,
                messages: tuple[ChatMessage, ...],
            ) -> int:
                return 0

        result = build_system_prompt(
            agent=sample_agent_with_personality,
            token_estimator=CountingEstimator(),
        )

        assert call_count > 0
        assert result.estimated_tokens > 0


# ── TestPolicyValidationIntegration ──────────────────────────────


@pytest.mark.unit
class TestPolicyValidationIntegration:
    """Tests for policy validation integration in build_system_prompt."""

    def test_policy_validation_error_does_not_block_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """When validate_policy_quality raises, prompt is still built."""
        from unittest.mock import patch

        with patch(
            "synthorg.engine.prompt.validate_policy_quality",
            side_effect=RuntimeError("boom"),
        ):
            result = build_system_prompt(
                agent=sample_agent_with_personality,
                org_policies=("All responses must include correlation_id",),
            )

        # Prompt is still built despite validation failure.
        assert result.content
        assert "org_policies" in result.sections

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "policies",
        [
            ("valid policy must exist", ""),
            ("   ",),
        ],
        ids=["empty_string", "whitespace_only"],
    )
    def test_invalid_org_policy_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        *,
        policies: tuple[str, ...],
    ) -> None:
        """Empty or whitespace-only policy is rejected with PromptBuildError."""
        with pytest.raises(PromptBuildError, match="org_policies"):
            build_system_prompt(
                agent=sample_agent_with_personality,
                org_policies=policies,
            )


# ── TestPromptVersioning ─────────────────────────────────────────


@pytest.mark.unit
class TestPromptVersioning:
    """Tests for prompt versioning and section tracking."""

    def test_template_version_frozen(self) -> None:
        """PROMPT_TEMPLATE_VERSION is '1.1.0' after strategy section addition."""
        assert PROMPT_TEMPLATE_VERSION == "1.1.0"

    def test_template_version_in_result(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """SystemPrompt.template_version matches the constant."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        assert result.template_version == PROMPT_TEMPLATE_VERSION

    def test_sections_tracked(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        sample_tool_definitions: tuple[ToolDefinition, ...],
        sample_company: Company,
    ) -> None:
        """Sections tuple lists all included sections (tools excluded per D22)."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            available_tools=sample_tool_definitions,
            company=sample_company,
        )

        assert "identity" in result.sections
        assert "personality" in result.sections
        assert "skills" in result.sections
        assert "authority" in result.sections
        assert "autonomy" in result.sections
        assert "task" in result.sections
        assert "tools" not in result.sections
        assert "company" in result.sections


# ── TestSystemPromptModel ────────────────────────────────────────


@pytest.mark.unit
class TestSystemPromptModel:
    """Tests for the SystemPrompt Pydantic model."""

    def test_frozen(self) -> None:
        """SystemPrompt instances are immutable."""
        prompt = SystemPrompt(
            content="test content",
            template_version="1.0.0",
            estimated_tokens=3,
            sections=("identity",),
            metadata={"agent_id": "abc"},
        )
        with pytest.raises(ValidationError):
            prompt.content = "modified"  # type: ignore[misc]

    def test_metadata_contains_all_agent_info(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Metadata contains expected keys with correct values."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        agent = sample_agent_with_personality

        assert result.metadata == {
            "agent_id": str(agent.id),
            "name": agent.name,
            "role": agent.role,
            "department": agent.department,
            "level": agent.level.value,
            "profile_tier": "large",
        }


# ── TestPromptLogging ────────────────────────────────────────────


@pytest.mark.unit
class TestPromptLogging:
    """Tests for structured logging during prompt construction."""

    def test_build_logs_start_and_success(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Build logs prompt.build.start and prompt.build.success events."""
        with structlog.testing.capture_logs() as logs:
            build_system_prompt(agent=sample_agent_with_personality)

        events = [entry["event"] for entry in logs]
        assert PROMPT_BUILD_START in events
        assert PROMPT_BUILD_SUCCESS in events

    def test_trim_logs_warning(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        sample_company: Company,
    ) -> None:
        """Token trimming logs a warning with the trimmed section names."""
        with structlog.testing.capture_logs() as logs:
            build_system_prompt(
                agent=sample_agent_with_personality,
                task=sample_task_with_criteria,
                company=sample_company,
                max_tokens=10,
            )

        trim_entries = [e for e in logs if e["event"] == PROMPT_BUILD_TOKEN_TRIMMED]
        assert len(trim_entries) == 1
        assert "trimmed_sections" in trim_entries[0]
        assert "company" in trim_entries[0]["trimmed_sections"]


# ── TestPromptErrorHandling ─────────────────────────────────────


@pytest.mark.unit
class TestPromptErrorHandling:
    """Tests for error paths in prompt construction."""

    def test_invalid_custom_template_raises_prompt_build_error(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Syntactically invalid custom template raises PromptBuildError."""
        with pytest.raises(PromptBuildError, match="invalid Jinja2 syntax"):
            build_system_prompt(
                agent=sample_agent_with_personality,
                custom_template="{% if %}",
            )

    def test_invalid_template_preserves_exception_chain(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """PromptBuildError chains the original TemplateSyntaxError."""
        with pytest.raises(PromptBuildError) as exc_info:
            build_system_prompt(
                agent=sample_agent_with_personality,
                custom_template="{% if %}",
            )

        assert exc_info.value.__cause__ is not None

    def test_render_error_raises_prompt_build_error(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Template with undefined filter raises PromptBuildError at render time."""
        with pytest.raises(PromptBuildError, match="rendering failed"):
            build_system_prompt(
                agent=sample_agent_with_personality,
                custom_template="{{ agent_name | nonexistent_filter }}",
            )


# ── TestTrimmingPriority ────────────────────────────────────────


@pytest.mark.unit
class TestTrimmingPriority:
    """Tests for section trimming priority order."""

    def test_company_trimmed_before_task(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        sample_company: Company,
    ) -> None:
        """With a moderately tight budget, only company is trimmed first."""
        # Build full prompt to get its token count.
        full = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            company=sample_company,
        )
        assert "company" in full.sections

        # Build without company to find a budget that fits without company
        # but not with it.
        without_company = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # Set max_tokens between without-company and full sizes.
        budget = (without_company.estimated_tokens + full.estimated_tokens) // 2
        trimmed = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            company=sample_company,
            max_tokens=budget,
        )

        # Company should be trimmed but task remains.
        assert "company" not in trimmed.sections
        assert "task" in trimmed.sections

    def test_trimming_order_without_tools(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        sample_company: Company,
    ) -> None:
        """Trimming order is company → task → org_policies (no tools section)."""
        # Build with company + task + org_policies.
        org_policies = ("All responses must include correlation_id",)
        full = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            company=sample_company,
            org_policies=org_policies,
        )
        assert "company" in full.sections
        assert "task" in full.sections
        assert "org_policies" in full.sections

        # With very tight budget, all optional sections are removed.
        trimmed = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            company=sample_company,
            org_policies=org_policies,
            max_tokens=10,
        )
        assert "company" not in trimmed.sections
        assert "task" not in trimmed.sections
        assert "org_policies" not in trimmed.sections
        assert "identity" in trimmed.sections


# ── TestDefaultAgentPrompt ─────────────────────────────────────


@pytest.mark.unit
class TestDefaultAgentPrompt:
    """Tests for agents with minimal/default configuration."""

    def test_empty_optional_fields_render_without_error(self) -> None:
        """Agent with default personality renders without errors."""
        agent = AgentIdentity(
            name="Default Agent",
            role="Worker",
            department="General",
            model=ModelConfig(provider="test", model_id="test-001"),
            hiring_date=date(2026, 1, 1),
        )
        result = build_system_prompt(agent=agent)

        assert "Default Agent" in result.content
        assert "Traits" not in result.content
        assert result.estimated_tokens > 0

    def test_task_with_zero_budget_and_no_deadline(self) -> None:
        """Task with zero budget and no deadline omits those sections."""
        from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
        from synthorg.core.task import Task

        task = Task(
            id="task-zero-001",
            title="Research task",
            description="Investigate options.",
            type=TaskType.RESEARCH,
            priority=Priority.LOW,
            project="proj-001",
            created_by="pm",
            estimated_complexity=Complexity.SIMPLE,
            budget_limit=0.0,
            assigned_to="worker",
            status=TaskStatus.ASSIGNED,
        )
        agent = AgentIdentity(
            name="Researcher",
            role="Analyst",
            department="Research",
            model=ModelConfig(provider="test", model_id="test-001"),
            hiring_date=date(2026, 1, 1),
        )
        result = build_system_prompt(agent=agent, task=task)

        assert "Research task" in result.content
        assert "Task budget" not in result.content
        assert "Deadline" not in result.content


# ── TestBudgetExceeded ─────────────────────────────────────────


@pytest.mark.unit
class TestBudgetExceeded:
    """Tests for budget-exceeded warning and max_tokens validation."""

    def test_budget_exceeded_logs_warning(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """When prompt exceeds max_tokens after trimming, log budget_exceeded."""
        with structlog.testing.capture_logs() as logs:
            build_system_prompt(
                agent=sample_agent_with_personality,
                max_tokens=1,
            )

        exceeded_entries = [
            e for e in logs if e["event"] == "prompt.build.budget_exceeded"
        ]
        assert len(exceeded_entries) == 1
        assert exceeded_entries[0]["max_tokens"] == 1

    def test_max_tokens_zero_raises_error(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """max_tokens=0 raises PromptBuildError."""
        with pytest.raises(PromptBuildError, match="max_tokens must be > 0"):
            build_system_prompt(
                agent=sample_agent_with_personality,
                max_tokens=0,
            )

    def test_max_tokens_negative_raises_error(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Negative max_tokens raises PromptBuildError."""
        with pytest.raises(PromptBuildError, match="max_tokens must be > 0"):
            build_system_prompt(
                agent=sample_agent_with_personality,
                max_tokens=-1,
            )


# ── TestBuildErrorPrompt ──────────────────────────────────────


@pytest.mark.unit
class TestBuildErrorPrompt:
    """Tests for the build_error_prompt() fallback function."""

    def test_returns_existing_prompt_when_provided(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """When system_prompt is not None, it is returned as-is."""
        existing = build_system_prompt(agent=sample_agent_with_personality)
        result = build_error_prompt(
            sample_agent_with_personality,
            "override-id",
            existing,
        )
        assert result is existing

    def test_returns_placeholder_when_no_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """When system_prompt is None, a placeholder is returned."""
        result = build_error_prompt(
            sample_agent_with_personality,
            "custom-agent-id",
            None,
        )
        assert result.content == ""
        assert result.template_version == "error"
        assert result.metadata["agent_id"] == "custom-agent-id"
        assert result.metadata["name"] == sample_agent_with_personality.name


# ── TestCatchAllExceptionWrapping ──────────────────────────────


@pytest.mark.unit
class TestCatchAllExceptionWrapping:
    """Tests for the catch-all exception handler in build_system_prompt."""

    def test_unexpected_error_wrapped_in_prompt_build_error(
        self,
        sample_agent_with_personality: AgentIdentity,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-PromptBuildError exceptions are wrapped with context."""
        from synthorg.engine import prompt as prompt_module

        def _broken_render(*_args: object, **_kwargs: object) -> None:
            msg = "simulated failure"
            raise RuntimeError(msg)

        monkeypatch.setattr(prompt_module, "render_with_trimming", _broken_render)

        with pytest.raises(PromptBuildError, match="Unexpected error") as exc_info:
            build_system_prompt(agent=sample_agent_with_personality)

        assert isinstance(exc_info.value.__cause__, RuntimeError)


# ── TestEffectiveAutonomyInPrompt ──────────────────────────────


@pytest.mark.unit
class TestEffectiveAutonomyInPrompt:
    """Tests for effective autonomy info in the system prompt."""

    def test_autonomy_level_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Effective autonomy level appears in the rendered prompt."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SEMI,
            auto_approve_actions=frozenset({"code:read", "code:write"}),
            human_approval_actions=frozenset({"infra:deploy"}),
            security_agent=False,
        )
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            effective_autonomy=autonomy,
        )
        assert "semi" in result.content

    def test_auto_approve_actions_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Auto-approved actions are listed in the prompt."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.FULL,
            auto_approve_actions=frozenset({"code:read", "code:write"}),
            human_approval_actions=frozenset(),
            security_agent=False,
        )
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            effective_autonomy=autonomy,
        )
        assert "code:read" in result.content
        assert "code:write" in result.content

    def test_human_approval_actions_in_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Human-approval-required actions are listed in the prompt."""
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.SUPERVISED,
            auto_approve_actions=frozenset(),
            human_approval_actions=frozenset({"infra:deploy", "budget:spend"}),
            security_agent=False,
        )
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            effective_autonomy=autonomy,
        )
        assert "infra:deploy" in result.content
        assert "budget:spend" in result.content

    def test_no_autonomy_omits_section(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """When no effective_autonomy is provided, no autonomy level section."""
        result = build_system_prompt(agent=sample_agent_with_personality)
        assert "Autonomy level" not in result.content
        assert "Auto-approved actions" not in result.content


# ── TestPromptProfileIntegration ─────────────────────────────────


@pytest.mark.unit
class TestPromptProfileIntegration:
    """Tests for profile-driven prompt rendering via model_tier."""

    def test_model_tier_none_produces_full_prompt(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """No tier = full profile, backward compatible."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier=None,
        )
        p = sample_agent_with_personality.personality

        assert p.risk_tolerance.value in result.content
        assert p.creativity.value in result.content
        assert p.verbosity.value in result.content

    def test_small_tier_omits_org_policies(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Small tier profile excludes org policies from prompt."""
        policies = ("All code must be reviewed.", "Follow security guidelines.")
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            org_policies=policies,
            model_tier="small",
        )

        assert "Organizational Policies" not in result.content
        assert "All code must be reviewed" not in result.content
        assert "org_policies" not in result.sections

    def test_large_tier_includes_org_policies(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Large tier profile includes org policies in prompt."""
        policies = ("All code must be reviewed.",)
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            org_policies=policies,
            model_tier="large",
        )

        assert "Organizational Policies" in result.content
        assert "All code must be reviewed" in result.content
        assert "org_policies" in result.sections

    def test_small_tier_simplifies_acceptance_criteria(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Small tier renders acceptance criteria as flat semicolon line."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            model_tier="small",
        )

        # Should NOT have the full "### Acceptance Criteria" heading.
        assert "### Acceptance Criteria" not in result.content
        # Should have semicolon-joined flat format.
        assert "**Criteria**:" in result.content

    def test_large_tier_full_acceptance_criteria(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Large tier renders full nested acceptance criteria."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            model_tier="large",
        )

        assert "### Acceptance Criteria" in result.content

    def test_small_tier_minimal_personality(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Small tier shows only communication style, not enums."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier="small",
        )
        p = sample_agent_with_personality.personality

        assert p.communication_style in result.content
        # Behavioral enums should NOT appear.
        assert "Risk tolerance" not in result.content
        assert "Verbosity" not in result.content
        assert "Decision-making" not in result.content

    def test_medium_tier_condensed_personality(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Medium tier shows description + style + traits, no enums."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier="medium",
        )
        p = sample_agent_with_personality.personality

        assert p.description in result.content
        assert p.communication_style in result.content
        for trait in p.traits:
            assert trait in result.content
        # Behavioral enums should NOT appear in condensed mode.
        assert "Risk tolerance" not in result.content
        assert "Creativity" not in result.content

    @pytest.mark.unit
    @pytest.mark.parametrize("tier", ["large", "medium", "small"])
    def test_authority_always_present(
        self,
        sample_agent_with_personality: AgentIdentity,
        tier: str,
    ) -> None:
        """Authority section is never stripped by any profile."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier=tier,  # type: ignore[arg-type]
        )

        assert "## Authority" in result.content
        assert "authority" in result.sections

    @pytest.mark.unit
    @pytest.mark.parametrize("tier", ["large", "medium", "small"])
    def test_identity_always_present(
        self,
        sample_agent_with_personality: AgentIdentity,
        tier: str,
    ) -> None:
        """Identity section is never stripped by any profile."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier=tier,  # type: ignore[arg-type]
        )

        assert "## Identity" in result.content
        assert sample_agent_with_personality.name in result.content
        assert "identity" in result.sections

    def test_profile_tier_in_metadata(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Metadata includes profile_tier when profile is applied."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier="medium",
        )

        assert result.metadata["profile_tier"] == "medium"

    def test_small_prompt_shorter_than_large(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Small tier prompt uses fewer tokens than large tier."""
        policies = ("All code must be reviewed.", "Follow security guidelines.")
        large = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            org_policies=policies,
            model_tier="large",
        )
        small = build_system_prompt(
            agent=sample_agent_with_personality,
            task=sample_task_with_criteria,
            org_policies=policies,
            model_tier="small",
        )

        assert small.estimated_tokens < large.estimated_tokens

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("tier", "autonomy_map"),
        [
            ("large", AUTONOMY_INSTRUCTIONS),
            ("medium", AUTONOMY_SUMMARY),
            ("small", AUTONOMY_MINIMAL),
        ],
    )
    def test_autonomy_text_varies_by_tier(
        self,
        sample_agent_with_personality: AgentIdentity,
        tier: str,
        autonomy_map: Mapping[SeniorityLevel, str],
    ) -> None:
        """Each tier renders the matching autonomy instruction text."""
        result = build_system_prompt(
            agent=sample_agent_with_personality,
            model_tier=tier,  # type: ignore[arg-type]
        )
        expected = autonomy_map[sample_agent_with_personality.level]

        assert expected in result.content


# ── TestBuildCoreContextDefaults ─────────────────────────────────


@pytest.mark.unit
class TestBuildCoreContextDefaults:
    """Tests for build_core_context profile=None fallback defaults."""

    def test_none_profile_defaults_to_full(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """When profile is None, context uses full-profile defaults."""
        from synthorg.engine._prompt_helpers import build_core_context

        ctx, trim_info = build_core_context(
            sample_agent_with_personality,
            role=None,
        )

        assert ctx["personality_mode"] == "full"
        assert ctx["include_org_policies"] is True
        assert ctx["simplify_acceptance_criteria"] is False
        assert trim_info is None
