"""Sprint configuration model.

Sprint duration, backlog capacity, and the window the rolling velocity
average is computed over.
"""

from pydantic import BaseModel, ConfigDict, Field


class SprintConfig(BaseModel):
    """Agile sprint workflow configuration.

    Attributes:
        duration_days: Default sprint duration in days.
        max_tasks_per_sprint: Maximum tasks allowed in a sprint backlog.
        velocity_window: Number of recent sprints for rolling velocity
            average.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    duration_days: int = Field(
        default=14,
        ge=1,
        le=90,
        description="Default sprint duration in days",
    )
    max_tasks_per_sprint: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum tasks per sprint backlog",
    )
    velocity_window: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Sprints for rolling velocity average",
    )
