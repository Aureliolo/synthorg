"""Performance tracking configuration."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.time_window import DEFAULT_WINDOW_LABELS, parse_window_days
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

    @model_validator(mode="after")
    def _validate_window_labels(self) -> Self:
        """Ensure every window parses and the sequence widens.

        ``AgentHealthService`` derives "most recent" by taking the first
        populated window in declared order, so an out-of-order tuple does not
        fail: it silently answers with a wider window than the caller asked
        for, hiding a fresh regression behind older successes. An unparseable
        label is rejected here rather than at first compute, where it surfaces
        as a bare ``ValueError`` from deep inside the window strategy.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        days: list[int] = []
        for label in self.windows:
            parsed = parse_window_days(label)
            if parsed is None:
                msg = f"windows entry {label!r} is not a '<N>d' window label"
                raise ValueError(msg)
            days.append(parsed)
        if days != sorted(days) or len(set(days)) != len(days):
            msg = f"windows must be in strictly ascending day order, got {self.windows}"
            raise ValueError(msg)
        return self
