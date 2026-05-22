"""Configuration for stakes-aware model routing.

``StakesRoutingConfig`` carries the per-stakes quality floors, the
coordination-metrics nudge thresholds, the red-team stakes threshold,
and the ``strategy`` discriminator dispatched by ``build_stakes_router``.

The default floors track the calibrated tier bands of
:class:`~synthorg.budget.benchmark_stub.StubBenchmarkScoreProvider`
(small 72, medium 85, large 92): a low floor admits the cheapest tier,
a normal floor requires at least medium, and a high/critical floor
requires the large tier.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import Stakes
from synthorg.core.types import NotBlankStr  # noqa: TC001

# Per-stakes benchmark quality floors (0 to 100). A model tier is a
# candidate only when its benchmark score clears the floor for the
# subtask's stakes level.
_FLOOR_LOW: Final[float] = 72.0
_FLOOR_NORMAL: Final[float] = 80.0
_FLOOR_HIGH: Final[float] = 88.0
_FLOOR_CRITICAL: Final[float] = 88.0

# Coordination-metrics nudge thresholds. When recent runs for a task
# show error amplification above this ratio (multi-agent error rate
# divided by single-agent baseline) or overhead above this percentage,
# the routing tier is bumped one step up.
_ERROR_AMPLIFICATION_THRESHOLD: Final[float] = 1.5
_OVERHEAD_THRESHOLD_PERCENT: Final[float] = 50.0
_COORDINATION_LOOKBACK: Final[int] = 5

_FLOOR_MIN: Final[float] = 0.0
_FLOOR_MAX: Final[float] = 100.0


class QualityFloors(BaseModel):
    """Per-stakes minimum benchmark score a model tier must clear.

    Attributes:
        low: Floor for LOW-stakes subtasks.
        normal: Floor for NORMAL-stakes subtasks.
        high: Floor for HIGH-stakes subtasks.
        critical: Floor for CRITICAL-stakes subtasks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    low: float = Field(default=_FLOOR_LOW, ge=_FLOOR_MIN, le=_FLOOR_MAX)
    normal: float = Field(default=_FLOOR_NORMAL, ge=_FLOOR_MIN, le=_FLOOR_MAX)
    high: float = Field(default=_FLOOR_HIGH, ge=_FLOOR_MIN, le=_FLOOR_MAX)
    critical: float = Field(default=_FLOOR_CRITICAL, ge=_FLOOR_MIN, le=_FLOOR_MAX)

    @model_validator(mode="after")
    def _validate_floors_ordered(self) -> Self:
        """Reject floors that invert the stakes hierarchy.

        A lower-stakes subtask must never carry a higher quality bar than
        a higher-stakes one; otherwise routing would send cheap work to
        strong models and consequential work to weak ones.
        """
        if not self.low <= self.normal <= self.high <= self.critical:
            msg = (
                "quality floors must be non-decreasing across stakes: "
                f"low={self.low} <= normal={self.normal} <= "
                f"high={self.high} <= critical={self.critical}"
            )
            raise ValueError(msg)
        return self

    def for_stakes(self, stakes: Stakes) -> float:
        """Return the quality floor for *stakes*."""
        return {
            Stakes.LOW: self.low,
            Stakes.NORMAL: self.normal,
            Stakes.HIGH: self.high,
            Stakes.CRITICAL: self.critical,
        }[stakes]


class StakesRoutingConfig(BaseModel):
    """Configuration for the stakes-aware routing strategy.

    Attributes:
        strategy: Discriminator selecting the routing strategy
            (``"stakes_aware"`` default, or ``"flat"`` for the no-op
            control / opt-out).
        quality_floors: Per-stakes benchmark quality floors.
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
    quality_floors: QualityFloors = Field(
        default_factory=QualityFloors,
        description="Per-stakes benchmark quality floors",
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
