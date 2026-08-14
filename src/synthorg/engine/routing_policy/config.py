"""Configuration for stakes-aware model routing.

``StakesRoutingConfig`` carries the per-stakes capability floor, the
coordination-metrics nudge thresholds, the red-team stakes threshold, and the
``strategy`` discriminator dispatched by ``build_stakes_router``.

Routing maps each stakes level to a minimum capability (``basic`` <
``capable`` < ``expert``): low-stakes work may run on the weakest rung, while
high/critical work requires an expert. The capability of each configured model
is decided by the capability-assignment subsystem (heuristic classification
overlaid by operator / LLM overrides), not by a benchmark score.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.completion_enums import ReasoningEffort, reasoning_effort_rank
from synthorg.core.task_enums import Stakes
from synthorg.core.types import CapabilityLevel, NotBlankStr, capability_rank

# Per-stakes capability floor. Low-stakes work runs on the weakest rung;
# normal work needs at least a capable model; high and critical work require
# an expert. The requirement is a floor: a task always runs on a model at or
# above it, escalating when none qualifies.
_FLOOR_LOW: Final[CapabilityLevel] = "basic"
_FLOOR_NORMAL: Final[CapabilityLevel] = "capable"
_FLOOR_HIGH: Final[CapabilityLevel] = "expert"
_FLOOR_CRITICAL: Final[CapabilityLevel] = "expert"

# Coordination-metrics nudge thresholds. When recent runs for a task show error
# amplification above this ratio (multi-agent error rate divided by single-agent
# baseline) or overhead above this percentage, the required capability is
# bumped one rung.
_ERROR_AMPLIFICATION_THRESHOLD: Final[float] = 1.5
_OVERHEAD_THRESHOLD_PERCENT: Final[float] = 50.0
_COORDINATION_LOOKBACK: Final[int] = 5


class StakesCapabilityFloor(BaseModel):
    """Per-stakes minimum capability a task must run on.

    Attributes:
        low: Required capability for LOW-stakes subtasks.
        normal: Required capability for NORMAL-stakes subtasks.
        high: Required capability for HIGH-stakes subtasks.
        critical: Required capability for CRITICAL-stakes subtasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    low: CapabilityLevel = Field(default=_FLOOR_LOW)
    normal: CapabilityLevel = Field(default=_FLOOR_NORMAL)
    high: CapabilityLevel = Field(default=_FLOOR_HIGH)
    critical: CapabilityLevel = Field(default=_FLOOR_CRITICAL)

    @model_validator(mode="after")
    def _validate_floors_ordered(self) -> Self:
        """Reject capability floors that invert the stakes hierarchy.

        A lower-stakes subtask must never require a stronger rung than a
        higher-stakes one; otherwise routing would send cheap work to strong
        models and consequential work to weak ones.

        Returns:
            ``self`` unchanged when the floors are non-decreasing across the
            stakes ladder.

        Raises:
            ValueError: When the configured floors violate
                ``low <= normal <= high <= critical`` by capability rank.
        """
        ranks = (
            capability_rank(self.low),
            capability_rank(self.normal),
            capability_rank(self.high),
            capability_rank(self.critical),
        )
        if not ranks[0] <= ranks[1] <= ranks[2] <= ranks[3]:
            msg = (
                "stakes capability floors must be non-decreasing: "
                f"low={self.low} <= normal={self.normal} <= "
                f"high={self.high} <= critical={self.critical}"
            )
            raise ValueError(msg)
        return self

    def for_stakes(self, stakes: Stakes) -> CapabilityLevel:
        """Return the required minimum capability for *stakes*."""
        mapping: dict[Stakes, CapabilityLevel] = {
            Stakes.LOW: self.low,
            Stakes.NORMAL: self.normal,
            Stakes.HIGH: self.high,
            Stakes.CRITICAL: self.critical,
        }
        return mapping[stakes]


class StakesReasoning(BaseModel):
    """Per-stakes reasoning depth requested from the model.

    Higher-stakes work asks the model to think harder, not just run on a
    stronger rung. ``None`` for a stakes level leaves reasoning unset (the
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

    @model_validator(mode="after")
    def _validate_reasoning_ordered(self) -> Self:
        """Reject reasoning efforts that invert the stakes hierarchy.

        A lower-stakes subtask must never request deeper reasoning than a
        higher-stakes one; otherwise routing would spend thinking budget on
        trivial work and under-think consequential work. ``None`` (unset)
        ranks below any configured effort.

        Returns:
            ``self`` unchanged when the efforts are non-decreasing across the
            stakes ladder.

        Raises:
            ValueError: When the configured efforts violate
                ``low <= normal <= high <= critical`` by reasoning rank.
        """

        def rank(effort: ReasoningEffort | None) -> int:
            return -1 if effort is None else reasoning_effort_rank(effort)

        ranks = (
            rank(self.low),
            rank(self.normal),
            rank(self.high),
            rank(self.critical),
        )
        if not ranks[0] <= ranks[1] <= ranks[2] <= ranks[3]:
            msg = (
                "stakes reasoning efforts must be non-decreasing: "
                f"low={self.low} <= normal={self.normal} <= "
                f"high={self.high} <= critical={self.critical}"
            )
            raise ValueError(msg)
        return self

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
        stakes_capability_floors: Per-stakes required minimum capability.
        stakes_reasoning: Per-stakes reasoning depth requested from the model.
        red_team_min_stakes: Lowest stakes level that requires the
            red-team gate and forbids downgrading below the agent's
            configured capability.
        error_amplification_threshold: Coordination error-amplification
            ratio above which the capability floor is nudged up.
        overhead_threshold_percent: Coordination overhead percentage
            above which the capability floor is nudged up.
        coordination_lookback: Number of recent coordination records to
            inspect for the nudge.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(
        default="stakes_aware",
        description="Routing strategy discriminator",
    )
    stakes_capability_floors: StakesCapabilityFloor = Field(
        default_factory=StakesCapabilityFloor,
        description="Per-stakes required minimum capability",
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
        description="Error-amplification ratio that triggers a capability nudge",
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
