"""Shared fixtures for trust subsystem tests."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ToolAccessLevel
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    WindowMetrics,
)
from synthorg.security.trust.config import (
    MilestoneCriteria,
    ReVerificationConfig,
    TrustConfig,
    TrustThreshold,
    WeightedTrustWeights,
)
from synthorg.security.trust.enums import TrustStrategyType


def make_performance_snapshot(
    agent_id: str = "agent-001",
    *,
    quality: float = 8.0,
    success_rate: float = 0.9,
    tasks_completed: int = 15,
) -> AgentPerformanceSnapshot:
    """Build an ``AgentPerformanceSnapshot`` for testing.

    Args:
        agent_id: Agent identifier.
        quality: Overall quality score (0.0-10.0).
        success_rate: Task success rate (0.0-1.0).
        tasks_completed: Number of successfully completed tasks.

    Returns:
        A frozen performance snapshot.

    When ``success_rate == 0`` and ``tasks_completed > 0``, all tasks
    are treated as failed to keep metrics self-consistent.
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
        agent_id=agent_id,
        computed_at=datetime.now(UTC),
        windows=(window,),
        overall_quality_score=quality,
    )


@pytest.fixture
def trust_config() -> TrustConfig:
    """Default disabled trust configuration."""
    return TrustConfig()


@pytest.fixture
def weighted_config() -> TrustConfig:
    """Weighted strategy trust configuration with thresholds."""
    return TrustConfig(
        strategy=TrustStrategyType.WEIGHTED,
        initial_level=ToolAccessLevel.SANDBOXED,
        weights=WeightedTrustWeights(
            task_difficulty=0.3,
            completion_rate=0.25,
            error_rate=0.25,
            human_feedback=0.2,
        ),
        promotion_thresholds={
            "sandboxed_to_restricted": TrustThreshold(score=0.5),
            "restricted_to_standard": TrustThreshold(score=0.7),
            "standard_to_elevated": TrustThreshold(
                score=0.9,
                requires_human_approval=True,
            ),
        },
    )


@pytest.fixture
def milestone_config() -> TrustConfig:
    """Milestone strategy trust configuration."""
    return TrustConfig(
        strategy=TrustStrategyType.MILESTONE,
        initial_level=ToolAccessLevel.SANDBOXED,
        milestones={
            "sandboxed_to_restricted": MilestoneCriteria(
                tasks_completed=5,
                quality_score_min=6.0,
            ),
            "restricted_to_standard": MilestoneCriteria(
                tasks_completed=15,
                quality_score_min=7.0,
            ),
            "standard_to_elevated": MilestoneCriteria(
                tasks_completed=30,
                quality_score_min=8.0,
                auto_promote=False,
                requires_human_approval=True,
            ),
        },
        re_verification=ReVerificationConfig(
            enabled=True,
            interval_days=90,
            decay_on_idle_days=30,
            decay_on_error_rate=0.15,
        ),
    )
