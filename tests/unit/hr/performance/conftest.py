"""Unit test configuration and fixtures for performance tracking models."""

from datetime import UTC, datetime

from synthorg.core.enums import Complexity, TaskType
from synthorg.core.task import AcceptanceCriterion
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)


def make_task_metric(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    task_type: TaskType = TaskType.DEVELOPMENT,
    completed_at: datetime | None = None,
    is_success: bool = True,
    duration_seconds: float = 60.0,
    cost_usd: float = 0.5,
    turns_used: int = 5,
    tokens_used: int = 1000,
    quality_score: float | None = None,
    complexity: Complexity = Complexity.MEDIUM,
) -> TaskMetricRecord:
    """Build a TaskMetricRecord with sensible defaults."""
    return TaskMetricRecord(
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id),
        task_type=task_type,
        completed_at=completed_at or datetime.now(UTC),
        is_success=is_success,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
        turns_used=turns_used,
        tokens_used=tokens_used,
        quality_score=quality_score,
        complexity=complexity,
    )


def make_collab_metric(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    recorded_at: datetime | None = None,
    delegation_success: bool | None = None,
    delegation_response_seconds: float | None = None,
    conflict_constructiveness: float | None = None,
    meeting_contribution: float | None = None,
    loop_triggered: bool = False,
    handoff_completeness: float | None = None,
) -> CollaborationMetricRecord:
    """Build a CollaborationMetricRecord with sensible defaults."""
    return CollaborationMetricRecord(
        agent_id=NotBlankStr(agent_id),
        recorded_at=recorded_at or datetime.now(UTC),
        delegation_success=delegation_success,
        delegation_response_seconds=delegation_response_seconds,
        conflict_constructiveness=conflict_constructiveness,
        meeting_contribution=meeting_contribution,
        loop_triggered=loop_triggered,
        handoff_completeness=handoff_completeness,
    )


def make_acceptance_criterion(
    *,
    description: str = "All tests pass",
    met: bool = True,
) -> AcceptanceCriterion:
    """Build an AcceptanceCriterion with sensible defaults."""
    return AcceptanceCriterion(
        description=NotBlankStr(description),
        met=met,
    )
