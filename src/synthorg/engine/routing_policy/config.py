"""Configuration for stakes-aware model routing.

``StakesRoutingConfig`` carries the per-stakes required model tier, the
coordination-metrics nudge thresholds, the red-team stakes threshold, and the
``strategy`` discriminator dispatched by ``build_stakes_router``.

Routing maps each stakes level to a minimum model tier (``small`` < ``medium``
< ``large``): low-stakes work may run on the cheapest tier, while high/critical
work requires the strongest tier. The tier of each configured model is decided
by the tier-assignment subsystem (heuristic classification overlaid by operator
/ LLM overrides), not by a benchmark score.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task_enums import Stakes
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.engine.routing_policy.tiers import tier_rank

# Per-stakes required minimum model tier. Low-stakes work runs on the cheapest
# tier; normal work needs at least a mid model; high and critical work require
# the strongest tier. The requirement is the floor: a task always runs on a
# model at or above it, escalating when none qualifies.
_TIER_LOW: Final[ModelTier] = "small"
_TIER_NORMAL: Final[ModelTier] = "medium"
_TIER_HIGH: Final[ModelTier] = "large"
_TIER_CRITICAL: Final[ModelTier] = "large"

# Coordination-metrics nudge thresholds. When recent runs for a task show error
# amplification above this ratio (multi-agent error rate divided by single-agent
# baseline) or overhead above this percentage, the routing tier is bumped one
# step up.
_ERROR_AMPLIFICATION_THRESHOLD: Final[float] = 1.5
_OVERHEAD_THRESHOLD_PERCENT: Final[float] = 50.0
_COORDINATION_LOOKBACK: Final[int] = 5


class StakesTierRequirement(BaseModel):
    """Per-stakes minimum model tier a task must run on.

    Attributes:
        low: Required tier for LOW-stakes subtasks.
        normal: Required tier for NORMAL-stakes subtasks.
        high: Required tier for HIGH-stakes subtasks.
        critical: Required tier for CRITICAL-stakes subtasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    low: ModelTier = Field(default=_TIER_LOW)
    normal: ModelTier = Field(default=_TIER_NORMAL)
    high: ModelTier = Field(default=_TIER_HIGH)
    critical: ModelTier = Field(default=_TIER_CRITICAL)

    @model_validator(mode="after")
    def _validate_tiers_ordered(self) -> Self:
        """Reject tier requirements that invert the stakes hierarchy.

        A lower-stakes subtask must never require a stronger tier than a
        higher-stakes one; otherwise routing would send cheap work to strong
        models and consequential work to weak ones.

        Returns:
            ``self`` unchanged when the tiers are non-decreasing across the
            stakes ladder.

        Raises:
            ValueError: When the configured tiers violate
                ``low <= normal <= high <= critical`` by tier rank.
        """
        ranks = (
            tier_rank(self.low),
            tier_rank(self.normal),
            tier_rank(self.high),
            tier_rank(self.critical),
        )
        if not ranks[0] <= ranks[1] <= ranks[2] <= ranks[3]:
            msg = (
                "stakes tier requirements must be non-decreasing: "
                f"low={self.low} <= normal={self.normal} <= "
                f"high={self.high} <= critical={self.critical}"
            )
            raise ValueError(msg)
        return self

    def for_stakes(self, stakes: Stakes) -> ModelTier:
        """Return the required minimum tier for *stakes*."""
        mapping: dict[Stakes, ModelTier] = {
            Stakes.LOW: self.low,
            Stakes.NORMAL: self.normal,
            Stakes.HIGH: self.high,
            Stakes.CRITICAL: self.critical,
        }
        return mapping[stakes]


class StakesReasoning(BaseModel):
    """Per-stakes reasoning depth requested from the model.

    Higher-stakes work asks the model to think harder, not just run on a
    stronger tier. ``None`` for a stakes level leaves reasoning unset (the
    provider default), so routine work carries no extra thinking cost. The
    request is only honoured for a model that advertises reasoning support;
    otherwise it is dropped at the driver boundary.

    Attributes:
        low: Reasoning effort for LOW-stakes subtasks.
        normal: Reasoning effort for NORMAL-stakes subtasks.
        high: Reasoning effort for HIGH-stakes subtasks.
        critical: Reasoning effort for CRITICAL-stakes subtasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    low: ReasoningEffort | None = Field(default=None)
    normal: ReasoningEffort | None = Field(default=ReasoningEffort.LOW)
    high: ReasoningEffort | None = Field(default=ReasoningEffort.MEDIUM)
    critical: ReasoningEffort | None = Field(default=ReasoningEffort.HIGH)

    def for_stakes(self, stakes: Stakes) -> ReasoningEffort | None:
        """Return the reasoning effort for *stakes* (``None`` if unset)."""
        mapping: dict[Stakes, ReasoningEffort | None] = {
            Stakes.LOW: self.low,
            Stakes.NORMAL: self.normal,
            Stakes.HIGH: self.high,
            Stakes.CRITICAL: self.critical,
        }
        return mapping[stakes]


class StakesRoutingConfig(BaseModel):
    """Configuration for the stakes-aware routing strategy.

    Attributes:
        strategy: Discriminator selecting the routing strategy
            (``"stakes_aware"`` default, or ``"flat"`` for the no-op
            control / opt-out).
        stakes_tiers: Per-stakes required minimum model tier.
        stakes_reasoning: Per-stakes reasoning depth requested from the model.
        red_team_min_stakes: Lowest stakes level that requires the
            red-team gate and forbids downgrading below the agent's
            configured tier.
        error_amplification_threshold: Coordination error-amplification
            ratio above which the tier is nudged up.
        overhead_threshold_percent: Coordination overhead percentage
            above which the tier is nudged up.
        coordination_lookback: Number of recent coordination records to
            inspect for the nudge.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(
        default="stakes_aware",
        description="Routing strategy discriminator",
    )
    stakes_tiers: StakesTierRequirement = Field(
        default_factory=StakesTierRequirement,
        description="Per-stakes required minimum model tier",
    )
    stakes_reasoning: StakesReasoning = Field(
        default_factory=StakesReasoning,
        description="Per-stakes reasoning depth requested from the model",
    )
    red_team_min_stakes: Stakes = Field(
        default=Stakes.HIGH,
        description="Lowest stakes requiring the red-team gate",
    )
    error_amplification_threshold: float = Field(
        default=_ERROR_AMPLIFICATION_THRESHOLD,
        gt=0.0,
        description="Error-amplification ratio that triggers a tier nudge",
    )
    overhead_threshold_percent: float = Field(
        default=_OVERHEAD_THRESHOLD_PERCENT,
        ge=0.0,
        description="Coordination overhead percentage that triggers a nudge",
    )
    coordination_lookback: int = Field(
        default=_COORDINATION_LOOKBACK,
        ge=1,
        description="Recent coordination records inspected for the nudge",
    )
