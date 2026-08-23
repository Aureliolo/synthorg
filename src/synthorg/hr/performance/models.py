# module-kind: declarative
"""Performance tracking domain models.

Frozen Pydantic models for task metrics, trend detection, and
rolling-window aggregates.
"""

from typing import Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.budget.currency import CurrencyCode
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_PERFORMANCE_CURRENCY_INVARIANT_VIOLATED

logger = get_logger(__name__)


class TaskMetricRecord(BaseModel):
    """Record of a single task completion for performance tracking.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent who completed the task.
        task_id: Task identifier.
        task_type: Classification of the task.
        started_at: When the task started (None if not tracked).
        completed_at: When the task was completed.
        is_success: Whether the task completed successfully.
        run_outcome: Fine-grained run outcome (succeeded / empty / failed),
            distinguishing an empty run (finished, produced nothing) from a
            hard failure where ``is_success`` alone cannot. None for records
            that carry no classified outcome; when set it must agree with
            ``is_success`` (success iff SUCCEEDED).
        duration_seconds: Wall-clock execution time, None when not measured
            (e.g. a record sourced from a task state transition, which
            carries reliability but no execution telemetry).
        cost: Numeric cost of the task, denominated in ``currency``; None
            when not measured.
        currency: ISO 4217 currency code for ``cost``.
        turns_used: Number of LLM turns used, None when not measured.
        tokens_used: Total tokens consumed, None when not measured.
        quality_score: Quality score (0.0-10.0), None if not scored.
        complexity: Estimated task complexity.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent who completed the task")
    task_id: NotBlankStr = Field(description="Task identifier")
    task_type: TaskType = Field(description="Classification of the task")
    started_at: AwareDatetime | None = Field(
        default=None,
        description="When the task started (None if not tracked)",
    )
    completed_at: AwareDatetime = Field(description="When the task was completed")
    is_success: bool = Field(description="Whether the task completed successfully")
    run_outcome: RunOutcome | None = Field(
        default=None,
        description=(
            "Fine-grained run outcome (succeeded / empty / failed); None when "
            "unclassified. When set, agrees with ``is_success``."
        ),
    )
    duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Wall-clock execution time; None when not measured",
    )
    cost: float | None = Field(
        default=None,
        ge=0.0,
        description="Numeric cost of the task in ``currency``; None when not measured",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for ``cost``",
    )
    turns_used: int | None = Field(
        default=None,
        ge=0,
        description="Number of LLM turns used; None when not measured",
    )
    tokens_used: int | None = Field(
        default=None,
        ge=0,
        description="Total tokens consumed; None when not measured",
    )
    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Quality score (0.0-10.0)",
    )
    complexity: Complexity = Field(description="Estimated task complexity")

    @model_validator(mode="after")
    def _validate_temporal_ordering(self) -> Self:
        """Ensure started_at is before completed_at when both are set.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.started_at is not None and self.started_at >= self.completed_at:
            msg = (
                f"started_at ({self.started_at.isoformat()}) must be "
                f"before completed_at ({self.completed_at.isoformat()})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_outcome_agrees_with_success(self) -> Self:
        """Ensure ``run_outcome``, when set, agrees with ``is_success``.

        A stored record must not claim a SUCCEEDED outcome for a non-success
        run (or vice versa); an empty/failed run is never a success.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.run_outcome is not None:
            expected_success = self.run_outcome == RunOutcome.SUCCEEDED
            if self.is_success != expected_success:
                msg = (
                    f"run_outcome ({self.run_outcome.value}) disagrees with "
                    f"is_success ({self.is_success})"
                )
                raise ValueError(msg)
        return self


class TrendResult(BaseModel):
    """Result of a trend detection analysis.

    Attributes:
        metric_name: Name of the metric being trended.
        window_size: Time window label (e.g. '7d', '30d').
        direction: Detected trend direction.
        slope: Computed slope of the trend line.
        data_point_count: Number of data points used.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        currency: ISO 4217 currency code for ``avg_cost_per_task``.
            Required whenever ``avg_cost_per_task`` is set; the reverse
            is not enforced -- a snapshot may carry a configured currency
            ahead of any cost signal (e.g. a freshly provisioned agent
            whose window has produced tasks but no LLM spend).  See
            ``_validate_currency_presence`` for the validator contract.
        avg_completion_time_seconds: Average time, None if insufficient data.
        avg_tokens_per_task: Average tokens, None if insufficient data.
        success_rate: Task success rate (0.0-1.0), None if no tasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        description="Average cost per task, denominated in ``currency``",
    )
    currency: CurrencyCode | None = Field(
        default=None,
        description=(
            "ISO 4217 currency code for ``avg_cost_per_task``; ``None`` "
            "when ``avg_cost_per_task`` is ``None``"
        ),
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

    @model_validator(mode="after")
    def _validate_task_counts(self) -> Self:
        """Ensure tasks_completed + tasks_failed == data_point_count.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.tasks_completed + self.tasks_failed != self.data_point_count:
            msg = (
                f"tasks_completed ({self.tasks_completed}) + tasks_failed "
                f"({self.tasks_failed}) must equal data_point_count "
                f"({self.data_point_count})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_currency_presence(self) -> Self:
        """Require ``currency`` whenever ``avg_cost_per_task`` is set.

        The reverse direction is intentionally **not** enforced: a
        ``WindowMetrics`` snapshot may legitimately carry a configured
        currency tag ahead of any cost signal (for example, a freshly
        provisioned agent whose window has produced tasks but no LLM
        spend).  Forcing ``currency`` to ``None`` in that case would
        destroy the aggregation-time context downstream consumers rely
        on.  The load-bearing invariant is "cost implies currency"; the
        opposite is a type assertion that existing callers do not
        honour and whose stricter form would cascade through dozens of
        test factories for no observable robustness gain.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.avg_cost_per_task is not None and self.currency is None:
            msg = (
                "currency is required when avg_cost_per_task is set "
                f"(avg_cost_per_task={self.avg_cost_per_task})"
            )
            logger.warning(
                HR_PERFORMANCE_CURRENCY_INVARIANT_VIOLATED,
                avg_cost_per_task=self.avg_cost_per_task,
                currency=self.currency,
                window_size=self.window_size,
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
        overall_quality_score: Mean of the completion-oracle verdicts
            recorded on the agent's tasks in range, or ``None`` when no
            reviewed task is in range.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        description="Mean completion-oracle verdict score",
    )
