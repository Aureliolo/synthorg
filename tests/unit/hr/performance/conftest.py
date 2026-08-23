"""Unit test configuration and fixtures for performance tracking models."""

from datetime import UTC, datetime

from synthorg.core.task import AcceptanceCriterion
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import TaskMetricRecord


def make_task_metric(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    task_type: TaskType = TaskType.DEVELOPMENT,
    completed_at: datetime | None = None,
    is_success: bool = True,
    duration_seconds: float | None = 60.0,
    cost: float | None = 0.5,
    currency: str = "USD",
    turns_used: int | None = 5,
    tokens_used: int | None = 1000,
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
        cost=cost,
        currency=currency,
        turns_used=turns_used,
        tokens_used=tokens_used,
        quality_score=quality_score,
        complexity=complexity,
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
