"""Shared fixtures and factories for promotion unit tests."""

from datetime import UTC, date, datetime

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    WindowMetrics,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.promotion.config import (
    ModelMappingConfig,
    PromotionApprovalConfig,
    PromotionConfig,
    PromotionCriteriaConfig,
)
from synthorg.hr.registry import AgentRegistryService

# ── Builder Functions ───────────────────────────────────────────


def make_performance_snapshot(
    agent_id: str,
    *,
    quality: float = 8.0,
    success_rate: float = 0.9,
    tasks_completed: int = 15,
) -> AgentPerformanceSnapshot:
    """Build an AgentPerformanceSnapshot with a single window.

    The WindowMetrics validator requires
    ``tasks_completed + tasks_failed == data_point_count``,
    so we compute ``tasks_failed`` from the success rate.

    When ``success_rate == 0`` and ``tasks_completed > 0``, all tasks
    are treated as failed (total = tasks_completed, tasks_failed = total,
    tasks_completed = 0) to keep the metrics self-consistent.
    """
    if success_rate > 0:
        total = max(1, int(tasks_completed / success_rate))
        tasks_failed = total - tasks_completed
    elif tasks_completed > 0:
        # 0% success: every task is a failure
        total = tasks_completed
        tasks_failed = total
        tasks_completed = 0
    else:
        total = 0
        tasks_failed = 0

    window = WindowMetrics(
        window_size="30d",
        data_point_count=total,
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
        avg_quality_score=quality,
        success_rate=success_rate,
    )

    return AgentPerformanceSnapshot(
        agent_id=NotBlankStr(agent_id),
        computed_at=datetime.now(UTC),
        windows=(window,),
        overall_quality_score=quality,
    )


def make_agent_identity(
    *,
    name: str = "test-agent",
    level: SeniorityLevel = SeniorityLevel.MID,
    model_id: str = "test-small-001",
    role: str = "developer",
    department: str = "engineering",
) -> AgentIdentity:
    """Build an AgentIdentity with sensible defaults."""
    return AgentIdentity(
        name=name,
        role=role,
        department=department,
        level=level,
        model=ModelConfig(
            provider="test-provider",
            model_id=model_id,
        ),
        hiring_date=date(2026, 1, 15),
    )


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def registry() -> AgentRegistryService:
    """Create a fresh agent registry."""
    return AgentRegistryService()


@pytest.fixture
def tracker() -> PerformanceTracker:
    """Create a fresh performance tracker."""
    return PerformanceTracker()


@pytest.fixture
def promotion_config() -> PromotionConfig:
    """Create a default promotion config."""
    return PromotionConfig()


@pytest.fixture
def criteria_config() -> PromotionCriteriaConfig:
    """Create a default promotion criteria config."""
    return PromotionCriteriaConfig()


@pytest.fixture
def approval_config() -> PromotionApprovalConfig:
    """Create a default promotion approval config."""
    return PromotionApprovalConfig()


@pytest.fixture
def model_mapping_config() -> ModelMappingConfig:
    """Create a default model mapping config."""
    return ModelMappingConfig()
