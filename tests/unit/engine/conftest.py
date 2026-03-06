"""Unit test configuration and fixtures for engine modules."""

from datetime import date
from uuid import uuid4

import pytest

from ai_company.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from ai_company.core.company import Company, CompanyConfig, Department
from ai_company.core.enums import (
    Complexity,
    CreativityLevel,
    DepartmentName,
    Priority,
    RiskTolerance,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from ai_company.core.role import Authority, Role
from ai_company.core.task import AcceptanceCriterion, Task
from ai_company.engine.context import AgentContext
from ai_company.engine.task_execution import TaskExecution
from ai_company.providers.models import TokenUsage, ToolDefinition


@pytest.fixture
def sample_model_config() -> ModelConfig:
    """Vendor-agnostic model config for prompt testing."""
    return ModelConfig(provider="test-provider", model_id="test-model-001")


@pytest.fixture
def sample_agent_with_personality(sample_model_config: ModelConfig) -> AgentIdentity:
    """Agent with rich personality config for prompt testing."""
    return AgentIdentity(
        id=uuid4(),
        name="Ada Lovelace",
        role="Senior Backend Developer",
        department="Engineering",
        level=SeniorityLevel.SENIOR,
        personality=PersonalityConfig(
            traits=("analytical", "methodical", "detail-oriented"),
            communication_style="concise and technical",
            risk_tolerance=RiskTolerance.LOW,
            creativity=CreativityLevel.HIGH,
            description="A precise thinker who values correctness above all.",
        ),
        skills=SkillSet(
            primary=("python", "system-design"),
            secondary=("databases", "security"),
        ),
        authority=Authority(
            can_approve=("code_reviews", "design_docs"),
            reports_to="engineering_lead",
            can_delegate_to=("mid_developers",),
            budget_limit=10.0,
        ),
        model=sample_model_config,
        hiring_date=date(2026, 1, 15),
    )


@pytest.fixture
def sample_role_with_description() -> Role:
    """Role with description for prompt rendering tests."""
    return Role(
        name="Senior Backend Developer",
        department=DepartmentName.ENGINEERING,
        required_skills=("python", "apis"),
        authority_level=SeniorityLevel.SENIOR,
        description="Designs and implements backend services and APIs.",
    )


@pytest.fixture
def sample_task_with_criteria() -> Task:
    """Task with acceptance criteria and budget for prompt testing."""
    return Task(
        id="task-prompt-001",
        title="Implement authentication module",
        description="Create JWT-based authentication for the REST API.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-001",
        created_by="product_manager",
        acceptance_criteria=(
            AcceptanceCriterion(description="Login endpoint returns JWT token"),
            AcceptanceCriterion(description="Token refresh works correctly"),
        ),
        estimated_complexity=Complexity.MEDIUM,
        budget_limit=5.0,
        deadline="2026-04-01T00:00:00",
        assigned_to="ada_lovelace",
        status=TaskStatus.ASSIGNED,
    )


@pytest.fixture
def sample_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Tuple of tool definitions for prompt testing."""
    return (
        ToolDefinition(
            name="code_search",
            description="Search the codebase for patterns and symbols.",
        ),
        ToolDefinition(
            name="run_tests",
            description="Execute the project test suite.",
        ),
        ToolDefinition(
            name="file_editor",
            description="Read and edit source files.",
        ),
    )


@pytest.fixture
def sample_token_usage() -> TokenUsage:
    """Small token usage for testing cost accumulation."""
    return TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
    )


@pytest.fixture
def sample_task_execution(sample_task_with_criteria: Task) -> TaskExecution:
    """TaskExecution from sample task (starts at ASSIGNED)."""
    return TaskExecution.from_task(sample_task_with_criteria)


@pytest.fixture
def sample_agent_context(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> AgentContext:
    """AgentContext from sample agent + sample task."""
    return AgentContext.from_identity(
        sample_agent_with_personality,
        task=sample_task_with_criteria,
    )


@pytest.fixture
def sample_company() -> Company:
    """Company with departments for prompt testing."""
    return Company(
        name="Acme AI Corp",
        departments=(
            Department(name="Engineering", head="cto", budget_percent=50.0),
            Department(name="Product", head="cpo", budget_percent=20.0),
        ),
        config=CompanyConfig(budget_monthly=500.0),
    )
