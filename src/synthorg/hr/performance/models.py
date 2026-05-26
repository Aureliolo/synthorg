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
    computed_field,
    model_validator,
)

from synthorg.budget.currency import CurrencyCode  # noqa: TC001
from synthorg.core.enums import Complexity, TaskType  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection  # noqa: TC001
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
        duration_seconds: Wall-clock execution time.
        cost: Numeric cost of the task, denominated in ``currency``.
        currency: ISO 4217 currency code for ``cost``.
        turns_used: Number of LLM turns used.
        tokens_used: Total tokens consumed.
        quality_score: Quality score (0.0-10.0), None if not scored.
        complexity: Estimated task complexity.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
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
    duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock execution time",
    )
    cost: float = Field(
        ge=0.0,
        description="Numeric cost of the task, denominated in ``currency``",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for ``cost``",
    )
    turns_used: int = Field(ge=0, description="Number of LLM turns used")
    tokens_used: int = Field(ge=0, description="Total tokens consumed")
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
        interaction_summary: Text summary of the interaction for LLM
            calibration (None if not available).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    interaction_summary: NotBlankStr | None = Field(
        default=None,
        max_length=4096,
        description="Text summary of the interaction for LLM calibration",
    )


class QualityScoreResult(BaseModel):
    """Result of a quality scoring evaluation.

    Attributes:
        score: Overall quality score (0.0-10.0).
        strategy_name: Name of the scoring strategy used.
        breakdown: Score components as (name, value) pairs.
        confidence: Confidence in the score (0.0-1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    score: float = Field(ge=0.0, le=10.0, description="Overall quality score")
    strategy_name: NotBlankStr = Field(description="Scoring strategy used")
    breakdown: tuple[tuple[NotBlankStr, float], ...] = Field(
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    score: float = Field(ge=0.0, le=10.0, description="Overall collaboration score")
    strategy_name: NotBlankStr = Field(description="Scoring strategy used")
    component_scores: tuple[tuple[NotBlankStr, float], ...] = Field(
        default=(),
        description="Per-component scores as (name, value) pairs",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the score",
    )
    override_active: bool = Field(
        default=False,
        description="Whether a human override is active",
    )


class LlmCalibrationRecord(BaseModel):
    """Record of an LLM calibration sample for collaboration scoring.

    Attributes:
        id: Unique record identifier.
        agent_id: Agent being evaluated.
        sampled_at: When the LLM evaluation occurred.
        interaction_record_id: ID of the sampled CollaborationMetricRecord.
        llm_score: LLM-assigned collaboration score (0.0-10.0).
        behavioral_score: Behavioral strategy score at time of sampling.
        drift: Absolute difference between LLM and behavioral scores (computed).
        rationale: LLM's explanation for the score.
        model_used: Which LLM model was used for evaluation.
        cost: Numeric cost of the LLM call, denominated in ``currency``.
        currency: ISO 4217 currency code for ``cost``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique record identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent being evaluated")
    sampled_at: AwareDatetime = Field(
        description="When the LLM evaluation occurred",
    )
    interaction_record_id: NotBlankStr = Field(
        description="ID of the sampled CollaborationMetricRecord",
    )
    llm_score: float = Field(
        ge=0.0,
        le=10.0,
        description="LLM-assigned collaboration score",
    )
    behavioral_score: float = Field(
        ge=0.0,
        le=10.0,
        description="Behavioral strategy score at time of sampling",
    )

    @computed_field(description="Absolute difference between LLM and behavioral scores")  # type: ignore[prop-decorator]
    @property
    def drift(self) -> float:
        """Absolute difference between LLM and behavioral scores."""
        return round(abs(self.llm_score - self.behavioral_score), 4)

    rationale: NotBlankStr = Field(
        max_length=2048,
        description="LLM's explanation for the score",
    )
    model_used: NotBlankStr = Field(
        description="Which LLM model was used for evaluation",
    )
    cost: float = Field(
        ge=0.0,
        description="Numeric cost of the LLM call, denominated in ``currency``",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code for ``cost``",
    )


class _BaseOverride(BaseModel):
    """Shared base for human-applied score overrides.

    Attributes:
        id: Unique override identifier.
        agent_id: Agent whose score is overridden.
        score: Override score (0.0-10.0).
        reason: Why the override was applied.
        applied_by: Identity of the human who applied it.
        applied_at: When the override was applied.
        expires_at: When the override expires (None = indefinite).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique override identifier",
    )
    agent_id: NotBlankStr = Field(
        description="Agent whose score is overridden",
    )
    score: float = Field(
        ge=0.0,
        le=10.0,
        description="Override score",
    )
    reason: NotBlankStr = Field(
        max_length=4096,
        description="Why the override was applied",
    )
    applied_by: NotBlankStr = Field(
        description="Identity of the human who applied it",
    )
    applied_at: AwareDatetime = Field(
        description="When the override was applied",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="When the override expires (None = indefinite)",
    )

    @model_validator(mode="after")
    def _validate_expiration_ordering(self) -> Self:
        """Ensure expires_at is strictly after applied_at when set.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.expires_at is not None and self.expires_at <= self.applied_at:
            msg = (
                f"expires_at ({self.expires_at}) must be after "
                f"applied_at ({self.applied_at})"
            )
            raise ValueError(msg)
        return self


class CollaborationOverride(_BaseOverride):
    """Human-applied override for an agent's collaboration score."""


class QualityOverride(_BaseOverride):
    """Human-applied override for an agent's quality score."""


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
        collaboration_score: Collaboration score, None if not computed.
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
    collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Collaboration score",
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


class CollaborationCalibration(BaseModel):
    """Stable, ops-facing readout of the collaboration scoring strategy.

    Returned by ``PerformanceTracker.get_collaboration_calibration`` for
    the MCP ``synthorg_collaboration_get_calibration`` tool. The shape
    is intentionally curated -- callers see ``strategy_name`` and the
    bounded ``component_weights`` tuple, but they do not see strategy-
    private internals. Swapping the underlying
    :class:`~synthorg.hr.performance.protocols.CollaborationScoringStrategy`
    therefore does not change the envelope shape MCP consumers depend on.

    Attributes:
        agent_id: The agent the calibration was computed for.
        strategy_name: Name of the active scoring strategy.
        window_sizes: Rolling-window labels the strategy aggregates over.
        component_weights: Ordered tuple of ``(component_name, weight)``
            pairs, where ``component_name`` is a stable identifier like
            ``"handoff_acceptance"``. A tuple-of-tuples (rather than a
            dict) is used so the wire shape is fully ordered and stays
            JSON-serialisable; callers that need lookup semantics should
            convert via ``dict(component_weights)`` at the call site.
        active_override: Currently-active human override, if any.
        sample_size: Number of collaboration samples backing the active
            window. ``0`` is valid (cold-start agents).
        last_calibrated_at: Timestamp of the most recent calibration
            sample, or ``None`` when no samples exist.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier")
    strategy_name: NotBlankStr = Field(description="Active strategy name")
    window_sizes: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Rolling-window labels aggregated over",
    )
    component_weights: tuple[tuple[NotBlankStr, float], ...] = Field(
        default=(),
        description="Per-component weights as (name, weight) pairs",
    )
    active_override: CollaborationOverride | None = Field(
        default=None,
        description="Active human override, when present",
    )
    sample_size: int = Field(
        ge=0,
        description="Number of collaboration samples available",
    )
    last_calibrated_at: AwareDatetime | None = Field(
        default=None,
        description="Timestamp of the most recent calibration sample",
    )


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
        description="Aggregate quality score",
    )
    overall_collaboration_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Aggregate collaboration score",
    )
