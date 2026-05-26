"""Performance inflection protocol and model.

Defines the ``PerformanceInflection`` event emitted when a
performance metric's trend direction changes, and the
``InflectionSink`` protocol for consumers of these events.
"""

from datetime import UTC, datetime
from typing import Protocol, Self, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.enums import TrendDirection  # noqa: TC001


class PerformanceInflection(BaseModel):
    """A change in trend direction for a performance metric.

    Emitted when the performance tracker detects that a metric's
    trend direction has changed (e.g., from stable to declining).

    Attributes:
        agent_id: Agent whose metric changed.
        metric_name: Name of the metric (e.g., "quality_score").
        window_size: Time window label (e.g., "7d").
        old_direction: Previous trend direction.
        new_direction: New trend direction.
        slope: Current trend slope value.
        detected_at: When the inflection was detected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    metric_name: NotBlankStr
    window_size: NotBlankStr
    old_direction: TrendDirection
    new_direction: TrendDirection
    slope: float
    detected_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_direction_change(self) -> Self:
        """Ensure old_direction and new_direction are different.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.old_direction == self.new_direction:
            msg = "old_direction and new_direction must be different"
            raise ValueError(msg)
        return self


@runtime_checkable
class InflectionSink(Protocol):
    """Consumer of performance inflection events.

    Implementations include the ``InflectionTrigger`` (which queues
    events for the evolution service) and any external monitoring
    integration.
    """

    async def emit(self, inflection: PerformanceInflection) -> None:
        """Receive a performance inflection event.

        Args:
            inflection: The inflection event to process.
        """
        ...
