"""Velocity calculation types and metrics models.

Defines the ``VelocityCalcType`` enum for selecting velocity calculators
and the ``VelocityMetrics`` model for strategy-computed velocity output.
"""

from collections.abc import Mapping  # noqa: TC003 -- Pydantic runtime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class VelocityCalcType(StrEnum):
    """Supported velocity calculator types.

    Each maps to a ``VelocityCalculator`` implementation that computes
    velocity metrics in a strategy-appropriate unit.

    Members:
        TASK_DRIVEN: Points per task completed.
        CALENDAR: Points per calendar day.
        MULTI_DIMENSIONAL: Points per sprint with secondary dimensions.
        BUDGET: Points per currency unit consumed.
        POINTS_PER_SPRINT: Simple points per sprint, no normalization.
    """

    TASK_DRIVEN = "task_driven"
    CALENDAR = "calendar"
    MULTI_DIMENSIONAL = "multi_dimensional"
    BUDGET = "budget"
    POINTS_PER_SPRINT = "points_per_sprint"


class VelocityMetrics(BaseModel):
    """Strategy-computed velocity output.

    Contains a primary metric (value + unit) and optional secondary
    metrics keyed by name.

    Attributes:
        primary_value: The primary velocity value.
        primary_unit: Human-readable unit label (e.g. ``"pts/task"``).
        secondary: Additional metrics keyed by name (e.g.
            ``{"pts_per_day": 3.2, "completion_ratio": 0.93}``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    primary_value: float = Field(
        ge=0.0,
        description="Primary velocity value",
    )
    primary_unit: NotBlankStr = Field(
        description="Human-readable unit label",
    )
    secondary: Mapping[str, float] = Field(
        default_factory=dict,
        description="Additional metrics keyed by name",
    )
