"""Evaluation framework configuration.

Per-pillar sub-configs with metric-level enable/disable toggles
and configurable weights. Weight redistribution for disabled metrics
is handled by the evaluation service and strategies at scoring time.

Shipped defaults: all pillars enabled with recommended weights.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import (
    NotBlankStr,
)


class IntelligenceConfig(BaseModel):
    """Intelligence/Accuracy pillar configuration.

    Attributes:
        enabled: Whether this pillar is active.
        weight: Pillar weight in overall evaluation (0.0-1.0).
        ci_quality_enabled: Enable CI signal quality metric.
        llm_calibration_enabled: Enable LLM calibration blend metric.
        ci_quality_weight: Weight for CI quality metric.
        llm_calibration_weight: Weight for LLM calibration metric.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    weight: float = Field(default=0.2, ge=0.0, le=1.0)
    ci_quality_enabled: bool = True
    llm_calibration_enabled: bool = True
    ci_quality_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    llm_calibration_weight: float = Field(default=0.3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_at_least_one_metric(self) -> Self:
        """Ensure at least one metric is enabled when pillar is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and not (
            self.ci_quality_enabled or self.llm_calibration_enabled
        ):
            msg = "At least one metric must be enabled when pillar is enabled"
            raise ValueError(msg)
        return self


class EfficiencyConfig(BaseModel):
    """Performance/Efficiency pillar configuration.

    Attributes:
        enabled: Whether this pillar is active.
        weight: Pillar weight in overall evaluation (0.0-1.0).
        cost_enabled: Enable cost efficiency metric.
        time_enabled: Enable time efficiency metric.
        tokens_enabled: Enable token efficiency metric.
        cost_weight: Weight for cost efficiency metric.
        time_weight: Weight for time efficiency metric.
        tokens_weight: Weight for token efficiency metric.
        reference_cost: Reference cost for normalization.
        reference_time_seconds: Reference completion time for normalization.
        reference_tokens: Reference token count for normalization.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    weight: float = Field(default=0.2, ge=0.0, le=1.0)
    cost_enabled: bool = True
    time_enabled: bool = True
    tokens_enabled: bool = True
    cost_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    time_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    tokens_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    reference_cost: float = Field(default=10.0, gt=0.0)
    reference_time_seconds: float = Field(default=300.0, gt=0.0)
    reference_tokens: int = Field(default=5000, gt=0)

    @model_validator(mode="after")
    def _validate_at_least_one_metric(self) -> Self:
        """Ensure at least one metric is enabled when pillar is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and not (
            self.cost_enabled or self.time_enabled or self.tokens_enabled
        ):
            msg = "At least one metric must be enabled when pillar is enabled"
            raise ValueError(msg)
        return self


class ResilienceConfig(BaseModel):
    """Reliability/Resilience pillar configuration.

    Attributes:
        enabled: Whether this pillar is active.
        weight: Pillar weight in overall evaluation (0.0-1.0).
        success_rate_enabled: Enable success rate metric.
        recovery_rate_enabled: Enable failure recovery metric.
        consistency_enabled: Enable quality consistency metric.
        streak_enabled: Enable success streak metric.
        success_rate_weight: Weight for success rate metric.
        recovery_rate_weight: Weight for recovery rate metric.
        consistency_weight: Weight for consistency metric.
        streak_weight: Weight for streak metric.
        streak_factor: Multiplier for streak score scaling.
        consistency_k: Sensitivity factor for stddev penalty.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    weight: float = Field(default=0.2, ge=0.0, le=1.0)
    success_rate_enabled: bool = True
    recovery_rate_enabled: bool = True
    consistency_enabled: bool = True
    streak_enabled: bool = True
    success_rate_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    recovery_rate_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    consistency_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    streak_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    streak_factor: float = Field(default=1.0, gt=0.0)
    consistency_k: float = Field(default=2.0, gt=0.0)

    @model_validator(mode="after")
    def _validate_at_least_one_metric(self) -> Self:
        """Ensure at least one metric is enabled when pillar is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and not (
            self.success_rate_enabled
            or self.recovery_rate_enabled
            or self.consistency_enabled
            or self.streak_enabled
        ):
            msg = "At least one metric must be enabled when pillar is enabled"
            raise ValueError(msg)
        return self


class GovernanceConfig(BaseModel):
    """Responsibility/Governance pillar configuration.

    Attributes:
        enabled: Whether this pillar is active.
        weight: Pillar weight in overall evaluation (0.0-1.0).
        audit_compliance_enabled: Enable audit compliance metric.
        trust_level_enabled: Enable trust level metric.
        autonomy_compliance_enabled: Enable autonomy compliance metric.
        audit_compliance_weight: Weight for audit compliance metric.
        trust_level_weight: Weight for trust level metric.
        autonomy_compliance_weight: Weight for autonomy compliance metric.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    weight: float = Field(default=0.2, ge=0.0, le=1.0)
    audit_compliance_enabled: bool = True
    trust_level_enabled: bool = True
    autonomy_compliance_enabled: bool = True
    audit_compliance_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    trust_level_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    autonomy_compliance_weight: float = Field(default=0.2, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_at_least_one_metric(self) -> Self:
        """Ensure at least one metric is enabled when pillar is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and not (
            self.audit_compliance_enabled
            or self.trust_level_enabled
            or self.autonomy_compliance_enabled
        ):
            msg = "At least one metric must be enabled when pillar is enabled"
            raise ValueError(msg)
        return self


class ExperienceConfig(BaseModel):
    """User Experience pillar configuration.

    Attributes:
        enabled: Whether this pillar is active.
        weight: Pillar weight in overall evaluation (0.0-1.0).
        clarity_enabled: Enable clarity rating metric.
        tone_enabled: Enable tone rating metric.
        helpfulness_enabled: Enable helpfulness rating metric.
        trust_enabled: Enable trust rating metric.
        satisfaction_enabled: Enable satisfaction rating metric.
        clarity_weight: Weight for clarity metric.
        tone_weight: Weight for tone metric.
        helpfulness_weight: Weight for helpfulness metric.
        trust_weight: Weight for trust metric.
        satisfaction_weight: Weight for satisfaction metric.
        min_feedback_count: Minimum feedback records for meaningful scoring.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    weight: float = Field(default=0.2, ge=0.0, le=1.0)
    clarity_enabled: bool = True
    tone_enabled: bool = True
    helpfulness_enabled: bool = True
    trust_enabled: bool = True
    satisfaction_enabled: bool = True
    clarity_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    tone_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    helpfulness_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    trust_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    satisfaction_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    min_feedback_count: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _validate_at_least_one_metric(self) -> Self:
        """Ensure at least one metric is enabled when pillar is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and not (
            self.clarity_enabled
            or self.tone_enabled
            or self.helpfulness_enabled
            or self.trust_enabled
            or self.satisfaction_enabled
        ):
            msg = "At least one metric must be enabled when pillar is enabled"
            raise ValueError(msg)
        return self


class EvalLoopConfig(BaseModel):
    """Configuration for the closed-loop evaluation coordinator.

    Attributes:
        enabled: Whether evaluation cycles are active.
        pattern_identifier_enabled: Enable pattern detection.
        benchmark_on_cycle: Run external benchmarks each cycle.
        max_concurrent_benchmarks: Limit parallel benchmark execution.
        pattern_weakness_threshold: Pillar score below which the pillar
            is considered weak for an agent (0.0-10.0). Default 5.0.
        pattern_min_agents: Minimum number of agents weak in a pillar
            before that pillar is emitted as a pattern. Default 3.
        pattern_action_map: Optional operator override for the
            pillar-to-action mapping used by ``_propose_actions``.
            Keys are ``EvaluationPillar`` values; values are free-form
            action identifiers. Unknown pillars fall back to the
            shipped default map.
        training_on_actions: When ``True``, a cycle that identifies
            corrective actions marks the cycle as training-triggered so
            the closed-loop runtime routes the actions to the training
            pipeline. Defaults to ``False`` so training -- an expensive,
            cost-incurring action -- is never fired without an explicit
            opt-in.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether evaluation cycles are active",
    )
    training_on_actions: bool = Field(
        default=False,
        description="Mark a cycle training-triggered when it proposes actions",
    )
    pattern_identifier_enabled: bool = Field(
        default=True,
        description="Enable pillar-weakness pattern identification",
    )
    benchmark_on_cycle: bool = Field(
        default=False,
        description="Run external benchmarks each cycle",
    )
    max_concurrent_benchmarks: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Limit parallel benchmark execution",
    )
    pattern_weakness_threshold: float = Field(
        default=5.0,
        ge=0.0,
        le=10.0,
        description="Pillar score below which the pillar is weak for an agent",
    )
    pattern_min_agents: int = Field(
        default=3,
        ge=1,
        description="Minimum weak-agent count before a pillar becomes a pattern",
    )
    pattern_action_map: dict[str, NotBlankStr] | None = Field(
        default=None,
        description=(
            "Operator override for pillar-to-action mapping. Values are "
            "validated as ``NotBlankStr`` so blank or whitespace-only "
            "action identifiers are rejected at config load."
        ),
    )


class EvaluationConfig(BaseModel):
    """Five-pillar evaluation framework configuration.

    Each pillar can be independently enabled/disabled. Within each pillar,
    individual metrics can be toggled. Disabled pillars/metrics have their
    weight redistributed to remaining enabled ones.

    Shipped defaults: all pillars enabled with recommended weights.

    Attributes:
        intelligence: Intelligence/Accuracy pillar configuration.
        efficiency: Performance/Efficiency pillar configuration.
        resilience: Reliability/Resilience pillar configuration.
        governance: Responsibility/Governance pillar configuration.
        experience: User Experience pillar configuration.
        calibration_drift_threshold: LLM calibration drift threshold
            for confidence reduction (0.0-10.0).
        eval_loop: Closed-loop evaluation coordinator configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intelligence: IntelligenceConfig = Field(
        default_factory=IntelligenceConfig,
    )
    efficiency: EfficiencyConfig = Field(
        default_factory=EfficiencyConfig,
    )
    resilience: ResilienceConfig = Field(
        default_factory=ResilienceConfig,
    )
    governance: GovernanceConfig = Field(
        default_factory=GovernanceConfig,
    )
    experience: ExperienceConfig = Field(
        default_factory=ExperienceConfig,
    )
    calibration_drift_threshold: float = Field(
        default=2.0,
        ge=0.0,
        le=10.0,
        description="LLM calibration drift threshold for confidence reduction",
    )
    eval_loop: EvalLoopConfig = Field(
        default_factory=EvalLoopConfig,
        description="Closed-loop evaluation coordinator configuration",
    )

    @model_validator(mode="after")
    def _validate_at_least_one_pillar_enabled(self) -> Self:
        """Ensure at least one pillar is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        pillars = [
            self.intelligence.enabled,
            self.efficiency.enabled,
            self.resilience.enabled,
            self.governance.enabled,
            self.experience.enabled,
        ]
        if not any(pillars):
            msg = "At least one evaluation pillar must be enabled"
            raise ValueError(msg)
        return self
