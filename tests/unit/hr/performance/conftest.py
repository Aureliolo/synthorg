"""Unit test configuration and fixtures for performance tracking models."""

from datetime import UTC, datetime

from synthorg.core.task import AcceptanceCriterion
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    CollaborationOverride,
    LlmCalibrationRecord,
    QualityOverride,
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
    cost: float = 0.5,
    currency: str = "USD",
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
        cost=cost,
        currency=currency,
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
    interaction_summary: NotBlankStr | None = None,
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
        interaction_summary=interaction_summary,
    )


def make_calibration_record(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    interaction_record_id: str = "record-001",
    sampled_at: datetime | None = None,
    llm_score: float = 7.5,
    behavioral_score: float = 6.0,
    rationale: str = "Good collaboration",
    model_used: str = "test-small-001",
    cost: float = 0.001,
    currency: str = "USD",
) -> LlmCalibrationRecord:
    """Build an LlmCalibrationRecord with sensible defaults."""
    return LlmCalibrationRecord(
        agent_id=NotBlankStr(agent_id),
        sampled_at=sampled_at or datetime.now(UTC),
        interaction_record_id=NotBlankStr(interaction_record_id),
        llm_score=llm_score,
        behavioral_score=behavioral_score,
        rationale=NotBlankStr(rationale),
        model_used=NotBlankStr(model_used),
        cost=cost,
        currency=currency,
    )


def make_collaboration_override(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    score: float = 8.0,
    reason: str = "Exceptional mentoring",
    applied_by: str = "manager-alice",
    applied_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> CollaborationOverride:
    """Build a CollaborationOverride with sensible defaults."""
    return CollaborationOverride(
        agent_id=NotBlankStr(agent_id),
        score=score,
        reason=NotBlankStr(reason),
        applied_by=NotBlankStr(applied_by),
        applied_at=applied_at or datetime.now(UTC),
        expires_at=expires_at,
    )


def make_quality_override(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    score: float = 8.0,
    reason: str = "Excellent task output quality",
    applied_by: str = "manager-alice",
    applied_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> QualityOverride:
    """Build a QualityOverride with sensible defaults."""
    return QualityOverride(
        agent_id=NotBlankStr(agent_id),
        score=score,
        reason=NotBlankStr(reason),
        applied_by=NotBlankStr(applied_by),
        applied_at=applied_at or datetime.now(UTC),
        expires_at=expires_at,
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
