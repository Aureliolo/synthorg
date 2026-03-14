"""Performance tracking domain models.

Frozen Pydantic models for task metrics, collaboration metrics,
quality/collaboration scoring results, trend detection, and
rolling-window aggregates.
"""

from typing import Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.enums import Complexity, TaskType  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection  # noqa: TC001


class TaskMetricRecord(BaseModel):
    """Record of a single task completion for performance tracking.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent who completed the task.
        task_id: Task identifier.
        task_type: Classification of the task.
        completed_at: When the task was completed.
        is_success: Whether the task completed successfully.
        duration_seconds: Wall-clock execution time.
        cost_usd: Cost of the task in USD.
        turns_used: Number of LLM turns used.
        tokens_used: Total tokens consumed.
        quality_score: Quality score (0.0-10.0), None if not scored.
        complexity: Estimated task complexity.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent who completed the task")
    task_id: NotBlankStr = Field(description="Task identifier")
    task_type: TaskType = Field(description="Classification of the task")
    completed_at: AwareDatetime = Field(description="When the task was completed")
    is_success: bool = Field(description="Whether the task completed successfully")
    duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock execution time",
    )
    cost_usd: float = Field(ge=0.0, description="Cost of the task in USD")
    turns_used: int = Field(ge=0, description="Number of LLM turns used")
    tokens_used: int = Field(ge=0, description="Total tokens consumed")
    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Quality score (0.0-10.0)",
    )
    complexity: Complexity = Field(description="Estimated task complexity")


class CollaborationMetricRecord(BaseModel):
    """Record of a collaboration behavior data point.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent being measured.
        recorded_at: When the observation was recorded.
        delegation_success: Whether a delegation was successful.
        delegation_response_seconds: Response time for a delegation.
        conflict_constructiveness: How constructively conflict was handled.
        meeting_contribution: Quality of meeting contribution.
        loop_triggered: Whether the agent triggered a delegation loop.
        handoff_completeness: Completeness of task handoff (0.0-1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent being measured")
    recorded_at: AwareDatetime = Field(
        description="When the observation was recorded",
    )
    delegation_success: bool | None = Field(
        default=None,
        description="Whether a delegation was successful",
    )
    delegation_response_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Response time for a delegation",
    )
    conflict_constructiveness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How constructively conflict was handled",
    )
    meeting_contribution: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Quality of meeting contribution",
    )
    loop_triggered: bool = Field(
        default=False,
        description="Whether the agent triggered a delegation loop",
    )
    handoff_completeness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Completeness of task handoff",
    )


class QualityScoreResult(BaseModel):
    """Result of a quality scoring evaluation.

    Attributes:
        score: Overall quality score (0.0-10.0).
        strategy_name: Name of the scoring strategy used.
        breakdown: Score components as (name, value) pairs.
        confidence: Confidence in the score (0.0-1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    score: float = Field(ge=0.0, le=10.0, description="Overall quality score")
    strategy_name: NotBlankStr = Field(description="Scoring strategy used")
    breakdown: tuple[tuple[str, float], ...] = Field(
        default=(),
        description="Score components as (name, value) pairs",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the score",
    )


class CollaborationScoreResult(BaseModel):
    """Result of a collaboration scoring evaluation.

    Attributes:
        score: Overall collaboration score (0.0-10.0).
        strategy_name: Name of the scoring strategy used.
        component_scores: Per-component scores as (name, value) pairs.
        confidence: Confidence in the score (0.0-1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    score: float = Field(ge=0.0, le=10.0, description="Overall collaboration score")
    strategy_name: NotBlankStr = Field(description="Scoring strategy used")
    component_scores: tuple[tuple[str, float], ...] = Field(
        default=(),
        description="Per-component scores as (name, value) pairs",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the score",
    )


class TrendResult(BaseModel):
    """Result of a trend detection analysis.

    Attributes:
        metric_name: Name of the metric being trended.
        window_size: Time window label (e.g. '7d', '30d').
        direction: Detected trend direction.
        slope: Computed slope of the trend line.
        data_point_count: Number of data points used.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    metric_name: NotBlankStr = Field(description="Metric being trended")
    window_size: NotBlankStr = Field(description="Time window label")
    direction: TrendDirection = Field(description="Detected trend direction")
    slope: float = Field(description="Slope of the trend line")
    data_point_count: int = Field(ge=0, description="Number of data points used")


class WindowMetrics(BaseModel):
    """Aggregate metrics for a rolling time window.

    Attributes:
        window_size: Time window label (e.g. '7d', '30d').
        data_point_count: Number of records in the window.
        tasks_completed: Number of successful tasks.
        tasks_failed: Number of failed tasks.
        avg_quality_score: Average quality score, None if insufficient data.
        avg_cost_per_task: Average cost per task, None if insufficient data.
        avg_completion_time_seconds: Average time, None if insufficient data.
        avg_tokens_per_task: Average tokens, None if insufficient data.
        success_rate: Task success rate (0.0-1.0), None if no tasks.
        collaboration_score: Collaboration score, None if not computed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    window_size: NotBlankStr = Field(description="Time window label")
    data_point_count: int = Field(ge=0, description="Records in the window")
    tasks_completed: int = Field(ge=0, description="Number of successful tasks")
    tasks_failed: int = Field(ge=0, description="Number of failed tasks")
    avg_quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Average quality score",
    )
    avg_cost_per_task: float | None = Field(
        default=None,
        ge=0.0,
        description="Average cost per task",
    )
    avg_completion_time_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Average completion time",
    )
    avg_tokens_per_task: float | None = Field(
        default=None,
        ge=0.0,
        description="Average tokens per task",
    )
    success_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Task success rate",
    )
    collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Collaboration score",
    )

    @model_validator(mode="after")
    def _validate_task_counts(self) -> Self:
        """Ensure tasks_completed + tasks_failed == data_point_count."""
        if self.tasks_completed + self.tasks_failed != self.data_point_count:
            msg = (
                f"tasks_completed ({self.tasks_completed}) + tasks_failed "
                f"({self.tasks_failed}) must equal data_point_count "
                f"({self.data_point_count})"
            )
            raise ValueError(msg)
        return self


class AgentPerformanceSnapshot(BaseModel):
    """Complete performance snapshot for an agent at a point in time.

    Attributes:
        agent_id: The agent being evaluated.
        computed_at: When this snapshot was computed.
        windows: Rolling window metrics.
        trends: Detected trends per metric.
        overall_quality_score: Aggregate quality score.
        overall_collaboration_score: Aggregate collaboration score.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent being evaluated")
    computed_at: AwareDatetime = Field(description="When this snapshot was computed")
    windows: tuple[WindowMetrics, ...] = Field(
        default=(),
        description="Rolling window metrics",
    )
    trends: tuple[TrendResult, ...] = Field(
        default=(),
        description="Detected trends per metric",
    )
    overall_quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Aggregate quality score",
    )
    overall_collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Aggregate collaboration score",
    )
