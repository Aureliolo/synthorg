"""Scaling configuration models.

Frozen Pydantic models defining per-strategy, trigger, and guard
configuration for the scaling service.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingStrategyName
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class WorkloadScalingConfig(BaseModel):
    """Configuration for the workload auto-scale strategy.

    Attributes:
        enabled: Whether this strategy is active.
        priority: Priority rank (lower = higher priority).
        hire_threshold: Utilization fraction above which to hire.
        prune_threshold: Utilization fraction below which to prune.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(default=True, description="Strategy enabled")
    priority: int = Field(default=3, ge=0, description="Priority rank")
    hire_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Hire when utilization exceeds this",
    )
    prune_threshold: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Prune when utilization drops below this",
    )

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> Self:
        """Ensure prune_threshold < hire_threshold.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.prune_threshold >= self.hire_threshold:
            msg = (
                f"prune_threshold ({self.prune_threshold}) "
                f"must be < hire_threshold ({self.hire_threshold})"
            )
            logger.warning(
                HR_SCALING_CONFIG_VALIDATION_FAILED,
                model="WorkloadScalingConfig",
                field="threshold_order",
                prune_threshold=self.prune_threshold,
                hire_threshold=self.hire_threshold,
            )
            raise ValueError(msg)
        return self


class BudgetCapConfig(BaseModel):
    """Configuration for the budget cap strategy.

    Attributes:
        enabled: Whether this strategy is active.
        priority: Priority rank (lower = higher priority).
        safety_margin: Burn rate fraction above which to prune.
        headroom_fraction: Burn rate below which hires are allowed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(default=True, description="Strategy enabled")
    priority: int = Field(default=0, ge=0, description="Priority rank")
    safety_margin: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Prune above this burn rate",
    )
    headroom_fraction: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Allow hires below this burn rate",
    )

    @model_validator(mode="after")
    def _validate_margin_order(self) -> Self:
        """Ensure headroom_fraction < safety_margin.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.headroom_fraction >= self.safety_margin:
            msg = (
                f"headroom_fraction ({self.headroom_fraction}) "
                f"must be < safety_margin ({self.safety_margin})"
            )
            logger.warning(
                HR_SCALING_CONFIG_VALIDATION_FAILED,
                model="BudgetCapConfig",
                field="margin_order",
                headroom_fraction=self.headroom_fraction,
                safety_margin=self.safety_margin,
            )
            raise ValueError(msg)
        return self


class SkillGapConfig(BaseModel):
    """Configuration for the skill gap strategy.

    Attributes:
        enabled: Whether this strategy is active.
        priority: Priority rank (lower = higher priority).
        min_missing_skills: Minimum missing skills to trigger.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Disabled by default (requires LLM)",
    )
    priority: int = Field(default=2, ge=0, description="Priority rank")
    min_missing_skills: int = Field(
        default=1,
        ge=1,
        description="Minimum missing skills to trigger hire",
    )


class PerformancePruningConfig(BaseModel):
    """Configuration for the performance pruning strategy.

    Attributes:
        enabled: Whether this strategy is active.
        priority: Priority rank (lower = higher priority).
        defer_during_evolution: Defer pruning during active evolution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(default=True, description="Strategy enabled")
    priority: int = Field(default=1, ge=0, description="Priority rank")
    defer_during_evolution: bool = Field(
        default=True,
        description="Defer pruning during active evolution",
    )


class TriggerConfig(BaseModel):
    """Configuration for scaling triggers.

    Attributes:
        batched_interval_seconds: Interval for the batched trigger.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    batched_interval_seconds: int = Field(
        default=900,
        ge=60,
        description="Batched trigger interval",
    )


class GuardConfig(BaseModel):
    """Configuration for scaling guards.

    Attributes:
        cooldown_seconds: Cooldown between same-type actions.
        max_hires_per_day: Daily hire cap.
        max_prunes_per_day: Daily prune cap.
        approval_expiry_days: Days until approval items expire.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cooldown_seconds: int = Field(
        default=3600,
        ge=0,
        description="Cooldown between same-type actions",
    )
    max_hires_per_day: int = Field(
        default=3,
        ge=0,
        description="Daily hire cap",
    )
    max_prunes_per_day: int = Field(
        default=1,
        ge=0,
        description="Daily prune cap",
    )
    approval_expiry_days: int = Field(
        default=7,
        ge=1,
        description="Approval item expiry",
    )


class ScalingConfig(BaseModel):
    """Master scaling configuration.

    Attributes:
        enabled: Whether the scaling service is active.
        workload: Workload strategy config.
        budget_cap: Budget cap strategy config.
        skill_gap: Skill gap strategy config.
        performance_pruning: Performance pruning strategy config.
        triggers: Trigger config.
        guards: Guard config.
        priority_order: Strategy priority (name list, first = highest).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(default=True, description="Scaling service enabled")
    default_hire_level: NotBlankStr = Field(
        default=NotBlankStr("mid"),
        description="Default seniority level for hire requests",
    )
    workload: WorkloadScalingConfig = Field(
        default_factory=WorkloadScalingConfig,
    )
    budget_cap: BudgetCapConfig = Field(default_factory=BudgetCapConfig)
    skill_gap: SkillGapConfig = Field(default_factory=SkillGapConfig)
    performance_pruning: PerformancePruningConfig = Field(
        default_factory=PerformancePruningConfig,
    )
    triggers: TriggerConfig = Field(default_factory=TriggerConfig)
    guards: GuardConfig = Field(default_factory=GuardConfig)
    priority_order: tuple[ScalingStrategyName, ...] = Field(
        default=(
            ScalingStrategyName.BUDGET_CAP,
            ScalingStrategyName.PERFORMANCE_PRUNING,
            ScalingStrategyName.SKILL_GAP,
            ScalingStrategyName.WORKLOAD,
        ),
        description="Strategy priority (first = highest)",
    )

    @model_validator(mode="after")
    def _validate_priority_order(self) -> Self:
        """Ensure priority_order contains unique strategy names.

        Omitted strategies are treated as lowest priority during conflict
        resolution (they receive ``_LOWEST_PRIORITY`` rank).

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if len(self.priority_order) != len(set(self.priority_order)):
            msg = "priority_order must not contain duplicates"
            logger.warning(
                HR_SCALING_CONFIG_VALIDATION_FAILED,
                model="ScalingConfig",
                field="priority_order",
                reason="duplicates",
            )
            raise ValueError(msg)
        return self
