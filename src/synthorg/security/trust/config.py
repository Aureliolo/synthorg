"""Trust configuration models.

Defines ``TrustConfig`` and strategy-specific sub-configs for
progressive trust evaluation.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import ToolAccessLevel
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.security.trust.enums import TrustStrategyType

logger = get_logger(__name__)

_WEIGHTS_SUM_TOLERANCE: Final[float] = 0.01


class TrustThreshold(BaseModel):
    """Threshold for a trust level transition.

    Attributes:
        score: Minimum score to trigger promotion.
        requires_human_approval: Whether human approval is required.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    score: float = Field(ge=0.0, le=1.0, description="Minimum score")
    requires_human_approval: bool = Field(
        default=False,
        description="Whether human approval is required",
    )


class WeightedTrustWeights(BaseModel):
    """Weights for the weighted trust score computation.

    Weights must sum to 1.0.

    Attributes:
        task_difficulty: Weight for task difficulty factor.
        completion_rate: Weight for completion rate factor.
        error_rate: Weight for error rate factor (inverse).
        human_feedback: Weight for human feedback factor.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_difficulty: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight for task difficulty",
    )
    completion_rate: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Weight for completion rate",
    )
    error_rate: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Weight for error rate (inverse)",
    )
    human_feedback: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Weight for human feedback",
    )

    @model_validator(mode="after")
    def _validate_weights_sum(self) -> Self:
        """Ensure weights sum to 1.0 (within tolerance)."""
        total = (
            self.task_difficulty
            + self.completion_rate
            + self.error_rate
            + self.human_feedback
        )
        tolerance = _WEIGHTS_SUM_TOLERANCE
        if abs(total - 1.0) > tolerance:
            msg = f"Trust weights must sum to 1.0, got {total:.4f}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="WeightedTrustWeights",
                total=total,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class CategoryTrustCriteria(BaseModel):
    """Promotion criteria for a single tool category.

    Attributes:
        tasks_completed: Minimum tasks completed in this category.
        quality_score_min: Minimum quality score.
        requires_human_approval: Whether human approval is required.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    tasks_completed: int = Field(
        default=10,
        ge=1,
        description="Minimum tasks completed",
    )
    quality_score_min: float = Field(
        default=7.0,
        ge=0.0,
        le=10.0,
        description="Minimum quality score",
    )
    requires_human_approval: bool = Field(
        default=False,
        description="Whether human approval is required",
    )


class MilestoneCriteria(BaseModel):
    """Criteria for a milestone-based trust promotion.

    Attributes:
        tasks_completed: Minimum tasks completed.
        quality_score_min: Minimum quality score.
        time_active_days: Minimum days active.
        auto_promote: Whether to auto-promote without human approval.
        clean_history_days: Days of clean (error-free) history required.
        requires_human_approval: Whether human approval is required.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    tasks_completed: int = Field(
        default=5,
        ge=1,
        description="Minimum tasks completed",
    )
    quality_score_min: float = Field(
        default=7.0,
        ge=0.0,
        le=10.0,
        description="Minimum quality score",
    )
    time_active_days: int = Field(
        default=0,
        ge=0,
        description="Minimum days active",
    )
    auto_promote: bool = Field(
        default=True,
        description="Whether to auto-promote",
    )
    clean_history_days: int = Field(
        default=0,
        ge=0,
        description="Days of clean history required",
    )
    requires_human_approval: bool = Field(
        default=False,
        description="Whether human approval is required",
    )

    @model_validator(mode="after")
    def _validate_approval_flags(self) -> Self:
        """Enforce mutual exclusivity of auto_promote and requires_human."""
        if self.auto_promote and self.requires_human_approval:
            msg = "auto_promote and requires_human_approval are mutually exclusive"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="MilestoneCriteria",
                auto_promote=self.auto_promote,
                requires_human_approval=self.requires_human_approval,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class ReVerificationConfig(BaseModel):
    """Configuration for periodic trust re-verification.

    Attributes:
        enabled: Whether re-verification is enabled.
        interval_days: Days between re-verifications.
        decay_on_idle_days: Demote one level after this many idle days.
        decay_on_error_rate: Demote if error rate exceeds this threshold.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    enabled: bool = Field(
        default=False,
        description="Whether re-verification is enabled",
    )
    interval_days: int = Field(
        default=90,
        ge=1,
        description="Days between re-verifications",
    )
    decay_on_idle_days: int = Field(
        default=30,
        ge=1,
        description="Idle days before trust decay",
    )
    decay_on_error_rate: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Error rate threshold for decay",
    )


class TrustConfig(BaseModel):
    """Top-level trust configuration.

    Attributes:
        strategy: Trust strategy type.
        initial_level: Default initial trust level for new agents.
        weights: Weights for the weighted strategy.
        promotion_thresholds: Thresholds for trust level transitions.
        initial_category_levels: Initial per-category levels (per_category).
        category_criteria: Per-category promotion criteria (per_category).
        milestones: Milestone criteria (used by milestone strategy).
        re_verification: Re-verification configuration (used by milestone strategy).
    """

    model_config = ConfigDict(frozen=True)

    strategy: TrustStrategyType = Field(
        default=TrustStrategyType.DISABLED,
        description="Trust strategy type",
    )
    initial_level: ToolAccessLevel = Field(
        default=ToolAccessLevel.STANDARD,
        description="Default initial trust level",
    )

    # Weighted strategy config
    weights: WeightedTrustWeights = Field(
        default_factory=WeightedTrustWeights,
        description="Weights for weighted strategy",
    )
    promotion_thresholds: dict[str, TrustThreshold] = Field(
        default_factory=dict,
        description="Thresholds for trust level transitions",
    )

    # Per-category strategy config
    initial_category_levels: dict[str, ToolAccessLevel] = Field(
        default_factory=dict,
        description="Initial per-category trust levels",
    )
    category_criteria: dict[str, dict[str, CategoryTrustCriteria]] = Field(
        default_factory=dict,
        description="Per-category promotion criteria",
    )

    # Milestone strategy config
    milestones: dict[str, MilestoneCriteria] = Field(
        default_factory=dict,
        description="Milestone criteria for trust transitions",
    )
    re_verification: ReVerificationConfig = Field(
        default_factory=ReVerificationConfig,
        description="Re-verification configuration",
    )

    @model_validator(mode="after")
    def _validate_elevated_gate(self) -> Self:
        """Enforce security invariant: standard_to_elevated always requires human.

        This is a hard security constraint that cannot be overridden
        regardless of strategy.
        """
        threshold_key = "standard_to_elevated"

        # Check promotion_thresholds (weighted strategy)
        if threshold_key in self.promotion_thresholds:
            threshold = self.promotion_thresholds[threshold_key]
            if not threshold.requires_human_approval:
                msg = (
                    "standard_to_elevated threshold must have "
                    "requires_human_approval=true (security invariant)"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    model="TrustConfig",
                    field="promotion_thresholds.standard_to_elevated",
                    reason=msg,
                )
                raise ValueError(msg)

        # Check milestones (milestone strategy)
        if threshold_key in self.milestones:
            milestone = self.milestones[threshold_key]
            if not milestone.requires_human_approval:
                msg = (
                    "standard_to_elevated milestone must have "
                    "requires_human_approval=true (security invariant)"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    model="TrustConfig",
                    field="milestones.standard_to_elevated",
                    reason=msg,
                )
                raise ValueError(msg)

        # Check category criteria — any category with standard_to_elevated
        for category, transitions in self.category_criteria.items():
            if threshold_key in transitions:
                criteria = transitions[threshold_key]
                if not criteria.requires_human_approval:
                    msg = (
                        f"standard_to_elevated criteria for category "
                        f"{category!r} must have "
                        f"requires_human_approval=true (security invariant)"
                    )
                    logger.warning(
                        CONFIG_VALIDATION_FAILED,
                        model="TrustConfig",
                        field=f"category_criteria.{category}.{threshold_key}",
                        reason=msg,
                    )
                    raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_strategy_specific_fields(self) -> Self:
        """Validate that active strategy has its required configuration."""
        if (
            self.strategy == TrustStrategyType.PER_CATEGORY
            and not self.initial_category_levels
        ):
            msg = "per_category strategy requires initial_category_levels to be set"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="TrustConfig",
                field="initial_category_levels",
                reason=msg,
            )
            raise ValueError(msg)

        if (
            self.strategy == TrustStrategyType.WEIGHTED
            and not self.promotion_thresholds
        ):
            msg = "weighted strategy requires at least one promotion_thresholds entry"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="TrustConfig",
                field="promotion_thresholds",
                reason=msg,
            )
            raise ValueError(msg)

        if self.strategy == TrustStrategyType.MILESTONE and not self.milestones:
            msg = "milestone strategy requires at least one milestones entry"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="TrustConfig",
                field="milestones",
                reason=msg,
            )
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_category_criteria_coverage(self) -> Self:
        """Ensure every category_criteria key has a matching initial level.

        Categories with criteria but no initial level would be silently
        skipped during evaluation.
        """
        if self.strategy != TrustStrategyType.PER_CATEGORY:
            return self

        uncovered = set(self.category_criteria.keys()) - set(
            self.initial_category_levels.keys()
        )
        if uncovered:
            msg = (
                f"category_criteria categories {sorted(uncovered)} "
                f"have no entry in initial_category_levels"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="TrustConfig",
                field="category_criteria",
                reason=msg,
            )
            raise ValueError(msg)

        return self
