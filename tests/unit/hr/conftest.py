"""Shared fixtures and factories for HR unit tests."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import (
    AgentStatus,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import FiringReason, HiringRequestStatus
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.models import CandidateCard, FiringRequest, HiringRequest
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService

# ── Model Config Helper ────────────────────────────────────────


def _default_model_config() -> ModelConfig:
    """Build a minimal ModelConfig for test agents."""
    return ModelConfig(
        provider="test-provider",
        model_id="test-small-001",
    )


# ── Builder Functions ───────────────────────────────────────────


def make_agent_identity(  # noqa: PLR0913
    *,
    name: str = "test-agent",
    role: str = "developer",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.MID,
    status: AgentStatus = AgentStatus.ACTIVE,
    hiring_date: date | None = None,
) -> AgentIdentity:
    """Build an AgentIdentity with sensible defaults."""
    return AgentIdentity(
        name=name,
        role=role,
        department=department,
        level=level,
        model=_default_model_config(),
        status=status,
        hiring_date=hiring_date or date(2026, 1, 15),
    )


def make_candidate_card(  # noqa: PLR0913
    *,
    name: str = "candidate-agent",
    role: str = "developer",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.MID,
    skills: tuple[str, ...] = (),
    rationale: str = "Needed for team expansion",
    estimated_monthly_cost: float = 50.0,
    template_source: str | None = None,
    candidate_id: str | None = None,
) -> CandidateCard:
    """Build a CandidateCard with sensible defaults."""
    kwargs: dict[str, Any] = {
        "name": name,
        "role": role,
        "department": department,
        "level": level,
        "skills": tuple(NotBlankStr(s) for s in skills),
        "rationale": rationale,
        "estimated_monthly_cost": estimated_monthly_cost,
        "template_source": template_source,
    }
    if candidate_id is not None:
        kwargs["id"] = candidate_id
    return CandidateCard(**kwargs)


def make_hiring_request(  # noqa: PLR0913
    *,
    requested_by: str = "cto",
    department: str = "engineering",
    role: str = "developer",
    level: SeniorityLevel = SeniorityLevel.MID,
    required_skills: tuple[str, ...] = (),
    reason: str = "Team needs more capacity",
    budget_limit_monthly: float | None = None,
    template_name: str | None = None,
    status: HiringRequestStatus = HiringRequestStatus.PENDING,
    candidates: tuple[CandidateCard, ...] = (),
    selected_candidate_id: str | None = None,
    request_id: str | None = None,
) -> HiringRequest:
    """Build a HiringRequest with sensible defaults."""
    kwargs: dict[str, Any] = {
        "requested_by": requested_by,
        "department": department,
        "role": role,
        "level": level,
        "required_skills": tuple(NotBlankStr(s) for s in required_skills),
        "reason": reason,
        "budget_limit_monthly": budget_limit_monthly,
        "template_name": template_name,
        "status": status,
        "candidates": candidates,
        "selected_candidate_id": selected_candidate_id,
        "created_at": datetime.now(UTC),
    }
    if request_id is not None:
        kwargs["id"] = request_id
    return HiringRequest(**kwargs)


def make_firing_request(
    *,
    agent_id: str = "agent-001",
    agent_name: str = "test-agent",
    reason: FiringReason = FiringReason.MANUAL,
    requested_by: str = "cto",
    details: str = "",
) -> FiringRequest:
    """Build a FiringRequest with sensible defaults."""
    return FiringRequest(
        agent_id=agent_id,
        agent_name=agent_name,
        reason=reason,
        requested_by=requested_by,
        details=details,
        created_at=datetime.now(UTC),
    )


def make_task(  # noqa: PLR0913
    *,
    task_id: str = "task-001",
    title: str = "Test task",
    description: str = "A test task",
    project: str = "test-project",
    created_by: str = "manager",
    status: TaskStatus = TaskStatus.CREATED,
    assigned_to: str | None = None,
) -> Task:
    """Build a Task with sensible defaults."""
    if assigned_to is None and status in {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
    }:
        assigned_to = "agent-001"
    return Task(
        id=task_id,
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        project=project,
        created_by=created_by,
        status=status,
        assigned_to=assigned_to,
    )


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def registry() -> AgentRegistryService:
    """Create a fresh agent registry."""
    return AgentRegistryService()


@pytest.fixture
def onboarding_service(registry: AgentRegistryService) -> OnboardingService:
    """Create an onboarding service with the shared registry."""
    return OnboardingService(registry=registry)


@pytest.fixture
def hiring_service(registry: AgentRegistryService) -> HiringService:
    """Create a hiring service with the shared registry (no approval store)."""
    return HiringService(registry=registry)
