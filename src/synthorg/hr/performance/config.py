"""Performance tracking configuration."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


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
    windows: tuple[NotBlankStr, ...] = Field(
        default=(
            NotBlankStr("7d"),
            NotBlankStr("30d"),
            NotBlankStr("90d"),
        ),
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

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure improving_threshold > declining_threshold."""
        if self.improving_threshold <= self.declining_threshold:
            msg = (
                f"improving_threshold ({self.improving_threshold}) must be "
                f"> declining_threshold ({self.declining_threshold})"
            )
            raise ValueError(msg)
        return self
