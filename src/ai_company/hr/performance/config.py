"""Performance tracking configuration."""

from pydantic import BaseModel, ConfigDict, Field


class PerformanceConfig(BaseModel):
    """Configuration for the performance tracking system.

    Attributes:
        min_data_points: Minimum data points for meaningful aggregation.
        windows: Time window labels for rolling metrics.
        improving_threshold: Slope threshold for improving trend.
        declining_threshold: Slope threshold for declining trend.
        collaboration_weights: Optional custom weights for collaboration
            scoring components.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    min_data_points: int = Field(
        default=5,
        ge=1,
        description="Minimum data points for meaningful aggregation",
    )
    windows: tuple[str, ...] = Field(
        default=("7d", "30d", "90d"),
        description="Time window labels for rolling metrics",
    )
    improving_threshold: float = Field(
        default=0.05,
        description="Slope threshold for improving trend",
    )
    declining_threshold: float = Field(
        default=-0.05,
        description="Slope threshold for declining trend",
    )
    collaboration_weights: dict[str, float] | None = Field(
        default=None,
        description="Custom weights for collaboration scoring components",
    )
