"""Performance tracking configuration."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.time_window import DEFAULT_WINDOW_LABELS
from synthorg.core.types import NotBlankStr


class PerformanceConfig(BaseModel):
    """Configuration for the performance tracking system.

    Attributes:
        min_data_points: Minimum data points for meaningful aggregation.
        windows: Time window labels for rolling metrics.
        improving_threshold: Slope threshold for improving trend.
        declining_threshold: Slope threshold for declining trend.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    min_data_points: int = Field(
        default=5,
        ge=1,
        description="Minimum data points for meaningful aggregation",
    )
    windows: tuple[NotBlankStr, ...] = Field(
        default_factory=lambda: tuple(NotBlankStr(w) for w in DEFAULT_WINDOW_LABELS),
        min_length=1,
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

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure improving_threshold > declining_threshold.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.improving_threshold <= self.declining_threshold:
            msg = (
                f"improving_threshold ({self.improving_threshold}) must be "
                f"> declining_threshold ({self.declining_threshold})"
            )
            raise ValueError(msg)
        return self
