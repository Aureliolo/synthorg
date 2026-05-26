"""Evaluation framework domain models.

Frozen Pydantic models for pillar scores, evaluation reports,
interaction feedback, resilience metrics, and the evaluation context
bag. Includes the ``redistribute_weights`` utility for proportional
weight redistribution when pillars or metrics are disabled.
"""

from collections.abc import (
    Sequence,  # noqa: TC003 - needed at runtime for PEP 649 VALUE format
)
from typing import Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvaluationConfig  # noqa: TC001
from synthorg.hr.evaluation.enums import EvaluationPillar  # noqa: TC001
from synthorg.hr.performance.models import (  # noqa: TC001
    AgentPerformanceSnapshot,
    LlmCalibrationRecord,
    TaskMetricRecord,
)


def redistribute_weights(
    items: Sequence[tuple[str, float, bool]],
) -> dict[str, float]:
    """Redistribute weights from disabled items to enabled ones.

    Takes a sequence of ``(name, weight, enabled)`` tuples and returns
    normalized weights for enabled items only. Disabled items are
    excluded; their weight is redistributed proportionally.

    When all enabled items have zero weight, equal distribution is used.

    Args:
        items: Sequence of (name, weight, enabled) tuples.

    Returns:
        Dictionary mapping enabled item names to normalized weights
        that sum to 1.0.

    Raises:
        ValueError: If all items are disabled.
    """
    enabled = [(name, w) for name, w, on in items if on]
    if not enabled:
        msg = "At least one item must be enabled"
        raise ValueError(msg)
    total = sum(w for _, w in enabled)
    if total == 0.0:
        equal = 1.0 / len(enabled)
        return {name: equal for name, _ in enabled}
    return {name: w / total for name, w in enabled}


class InteractionFeedback(BaseModel):
    """User experience feedback for an agent interaction.

    All ratings are normalized to 0.0-1.0 range. Strategies scale
    them to 0.0-10.0 during scoring. Each rating is optional to
    support partial feedback collection.

    Attributes:
        id: Unique feedback identifier.
        agent_id: Agent being rated.
        task_id: Associated task (None if general feedback).
        recorded_at: When the feedback was submitted.
        clarity_rating: How clear the agent's output was (0.0-1.0).
        tone_rating: How appropriate the tone was (0.0-1.0).
        helpfulness_rating: How helpful the output was (0.0-1.0).
        trust_rating: How trustworthy the agent felt (0.0-1.0).
        satisfaction_rating: Overall satisfaction (0.0-1.0).
        free_text: Optional free-text comment.
        source: Origin of the feedback (e.g. "human", "automated",
            "llm_judge").
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique feedback identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent being rated")
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Associated task (None if general feedback)",
    )
    recorded_at: AwareDatetime = Field(
        description="When the feedback was submitted",
    )
    clarity_rating: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How clear the agent's output was",
    )
    tone_rating: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How appropriate the tone was",
    )
    helpfulness_rating: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How helpful the output was",
    )
    trust_rating: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How trustworthy the agent felt",
    )
    satisfaction_rating: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Overall satisfaction",
    )
    free_text: str | None = Field(
        default=None,
        max_length=4096,
        description="Optional free-text comment",
    )
    source: NotBlankStr = Field(
        description='Origin of the feedback (e.g. "human", "automated")',
    )

    @model_validator(mode="after")
    def _validate_has_signal(self) -> Self:
        """Ensure at least one rating or non-blank free_text is present.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        has_rating = any(
            v is not None
            for v in (
                self.clarity_rating,
                self.tone_rating,
                self.helpfulness_rating,
                self.trust_rating,
                self.satisfaction_rating,
            )
        )
        has_text = self.free_text is not None and self.free_text.strip()
        if not has_rating and not has_text:
            msg = "Feedback must contain at least one rating or non-blank free_text"
            raise ValueError(msg)
        return self


class ResilienceMetrics(BaseModel):
    """Derived resilience metrics from task execution records.

    Computed by the evaluation service from raw task records
    before passing to the resilience scoring strategy.

    Attributes:
        total_tasks: Total task count in the evaluation window.
        failed_tasks: Number of failed tasks.
        recovered_tasks: Tasks that succeeded after a prior failure.
        current_success_streak: Current consecutive successes.
        longest_success_streak: Longest consecutive successes.
        quality_score_stddev: Standard deviation of quality scores
            (None if insufficient scored tasks).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_tasks: int = Field(ge=0, description="Total task count")
    failed_tasks: int = Field(ge=0, description="Number of failed tasks")
    recovered_tasks: int = Field(
        ge=0,
        description="Tasks that succeeded after a prior failure",
    )
    current_success_streak: int = Field(
        ge=0,
        description="Current consecutive successes",
    )
    longest_success_streak: int = Field(
        ge=0,
        description="Longest consecutive successes",
    )
    quality_score_stddev: float | None = Field(
        default=None,
        ge=0.0,
        description="Standard deviation of quality scores",
    )

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        """Ensure failed_tasks <= total_tasks and streaks are consistent.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.failed_tasks > self.total_tasks:
            msg = (
                f"failed_tasks ({self.failed_tasks}) cannot exceed "
                f"total_tasks ({self.total_tasks})"
            )
            raise ValueError(msg)
        if self.recovered_tasks > self.failed_tasks:
            msg = (
                f"recovered_tasks ({self.recovered_tasks}) cannot exceed "
                f"failed_tasks ({self.failed_tasks})"
            )
            raise ValueError(msg)
        if self.longest_success_streak < self.current_success_streak:
            msg = (
                f"longest_success_streak ({self.longest_success_streak}) "
                f"cannot be less than current_success_streak "
                f"({self.current_success_streak})"
            )
            raise ValueError(msg)
        return self


class PillarScore(BaseModel):
    """Score for a single evaluation pillar.

    Output of every pillar scoring strategy. Extends the pattern
    of ``QualityScoreResult`` with pillar identity, data provenance,
    and temporal metadata.

    Attributes:
        pillar: Which pillar this score represents.
        score: Overall pillar score (0.0-10.0).
        confidence: Confidence in the score (0.0-1.0).
        strategy_name: Name of the scoring strategy used.
        breakdown: Score components as (name, value) pairs.
        data_point_count: Number of data points used.
        evaluated_at: When this score was computed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pillar: EvaluationPillar = Field(description="Which pillar this score represents")
    score: float = Field(ge=0.0, le=10.0, description="Overall pillar score")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the score")
    strategy_name: NotBlankStr = Field(description="Scoring strategy used")
    breakdown: tuple[tuple[NotBlankStr, float], ...] = Field(
        default=(),
        description="Score components as (name, value) pairs",
    )
    data_point_count: int = Field(ge=0, description="Number of data points used")
    evaluated_at: AwareDatetime = Field(description="When this score was computed")


class EvaluationContext(BaseModel):
    """Input bag for all pillar scoring strategies.

    Built by the ``EvaluationService`` before scoring. Each strategy
    reads the fields it needs. Carries the ``EvaluationConfig`` so
    strategies can check their pillar-level metric toggles.

    Attributes:
        agent_id: Agent being evaluated.
        now: Reference timestamp for the evaluation.
        config: Evaluation configuration with pillar/metric toggles.
        snapshot: Performance snapshot from the tracker.
        task_records: Raw task metric records in the evaluation window.
        calibration_records: LLM calibration records for drift analysis.
        feedback: Interaction feedback records for UX scoring.
        resilience_metrics: Derived resilience metrics from task records.
        audit_allow_count: Allowed audit entries in the window.
        audit_deny_count: Denied audit entries in the window.
        audit_escalate_count: Escalated audit entries in the window.
        audit_high_risk_count: High-risk audit entries in the window.
        trust_level: Current trust level name (None if unknown).
        trust_demotions_in_window: Trust demotions in the window.
        autonomy_downgrades_in_window: Autonomy downgrades in the window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent being evaluated")
    now: AwareDatetime = Field(description="Reference timestamp")
    config: EvaluationConfig = Field(description="Evaluation configuration")
    snapshot: AgentPerformanceSnapshot = Field(
        description="Performance snapshot from the tracker",
    )
    task_records: tuple[TaskMetricRecord, ...] = Field(
        default=(),
        description="Raw task metric records",
    )
    calibration_records: tuple[LlmCalibrationRecord, ...] = Field(
        default=(),
        description="LLM calibration records",
    )
    feedback: tuple[InteractionFeedback, ...] = Field(
        default=(),
        description="Interaction feedback records",
    )
    resilience_metrics: ResilienceMetrics | None = Field(
        default=None,
        description="Derived resilience metrics",
    )
    audit_allow_count: int = Field(
        ge=0,
        default=0,
        description="Allowed audit entries in the window",
    )
    audit_deny_count: int = Field(
        ge=0,
        default=0,
        description="Denied audit entries in the window",
    )
    audit_escalate_count: int = Field(
        ge=0,
        default=0,
        description="Escalated audit entries in the window",
    )
    audit_high_risk_count: int = Field(
        ge=0,
        default=0,
        description="High-risk audit entries in the window",
    )
    trust_level: NotBlankStr | None = Field(
        default=None,
        description="Current trust level name",
    )
    trust_demotions_in_window: int = Field(
        ge=0,
        default=0,
        description="Trust demotions in the window",
    )
    autonomy_downgrades_in_window: int = Field(
        ge=0,
        default=0,
        description="Autonomy downgrades in the window",
    )

    @model_validator(mode="after")
    def _validate_agent_id_consistency(self) -> Self:
        """Ensure context agent_id matches snapshot agent_id.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.agent_id != self.snapshot.agent_id:
            msg = (
                f"Context agent_id ({self.agent_id}) does not match "
                f"snapshot agent_id ({self.snapshot.agent_id})"
            )
            raise ValueError(msg)
        return self


class EvaluationReport(BaseModel):
    """Complete five-pillar evaluation report for an agent.

    Composes pillar scores with the underlying performance snapshot.
    Only contains scores for enabled pillars.

    Attributes:
        id: Unique report identifier.
        agent_id: Agent that was evaluated.
        computed_at: When this report was generated.
        snapshot: Underlying performance snapshot.
        pillar_scores: Scores for each enabled pillar.
        overall_score: Weighted overall score (0.0-10.0).
        overall_confidence: Weighted overall confidence (0.0-1.0).
        pillar_weights: Applied weights as (pillar_name, weight) pairs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
        description="Unique report identifier",
    )
    agent_id: NotBlankStr = Field(description="Agent that was evaluated")
    computed_at: AwareDatetime = Field(description="When this report was generated")
    snapshot: AgentPerformanceSnapshot = Field(
        description="Underlying performance snapshot",
    )
    pillar_scores: tuple[PillarScore, ...] = Field(
        description="Scores for each enabled pillar",
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description="Weighted overall score",
    )
    overall_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Weighted overall confidence",
    )
    pillar_weights: tuple[tuple[NotBlankStr, float], ...] = Field(
        description="Applied weights as (pillar_name, weight) pairs",
    )

    @model_validator(mode="after")
    def _validate_unique_pillars(self) -> Self:
        """Ensure pillar scores have unique pillar names.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        names = [ps.pillar for ps in self.pillar_scores]
        if len(names) != len(set(names)):
            seen: set[EvaluationPillar] = set()
            dupes: list[str] = []
            for n in names:
                if n in seen:
                    dupes.append(n.value)
                seen.add(n)
            msg = f"Duplicate pillar scores: {', '.join(dupes)}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_agent_id_consistency(self) -> Self:
        """Ensure report agent_id matches snapshot agent_id.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.agent_id != self.snapshot.agent_id:
            msg = (
                f"Report agent_id ({self.agent_id}) does not match "
                f"snapshot agent_id ({self.snapshot.agent_id})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_weights_match_scores(self) -> Self:
        """Ensure pillar_weights entries correspond to pillar_scores.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        weight_names = [name for name, _ in self.pillar_weights]
        if len(weight_names) != len(set(weight_names)):
            msg = "Duplicate entries in pillar_weights"
            raise ValueError(msg)
        score_pillars = {ps.pillar.value for ps in self.pillar_scores}
        weight_pillars = set(weight_names)
        if score_pillars != weight_pillars:
            msg = (
                f"Pillar weight names {sorted(weight_pillars)} do not match "
                f"pillar score names {sorted(score_pillars)}"
            )
            raise ValueError(msg)
        return self
