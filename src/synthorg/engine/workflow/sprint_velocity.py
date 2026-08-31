"""Sprint velocity tracking -- the ``VelocityRecord`` model.

Recording velocity from completed sprints and computing rolling
averages was speculative future functionality: neither
``record_velocity`` nor ``calculate_average_velocity`` had a
production caller, so both were removed. The model stays, imported by
its re-export barrel.
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import NotBlankStr


class VelocityRecord(BaseModel):
    """Velocity snapshot from a completed sprint.

    Attributes:
        sprint_id: ID of the completed sprint.
        sprint_number: Sequential sprint number.
        story_points_committed: Points planned for the sprint.
        story_points_completed: Points actually delivered.
        duration_days: Sprint duration in days.
        completion_ratio: Ratio of completed to committed points
            (computed; 0.0 when nothing was committed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    sprint_id: NotBlankStr = Field(
        description="ID of the completed sprint",
    )
    sprint_number: int = Field(
        ge=1,
        description="Sequential sprint number",
    )
    story_points_committed: float = Field(
        ge=0.0,
        description="Points planned",
    )
    story_points_completed: float = Field(
        ge=0.0,
        description="Points delivered",
    )
    duration_days: int = Field(
        ge=1,
        description="Sprint duration in days",
    )
    task_completion_count: int | None = Field(
        default=None,
        ge=0,
        description="Tasks completed in the sprint",
    )
    wall_clock_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Real elapsed time in seconds",
    )
    budget_consumed: float | None = Field(
        default=None,
        ge=0.0,
        description="Cost consumed during the sprint",
    )

    @computed_field
    @property
    def completion_ratio(self) -> float:
        """Ratio of completed to committed points.

        Values above 1.0 are valid and indicate that the team
        completed more work than initially committed (scope
        expansion during the sprint).
        """
        if self.story_points_committed == 0.0:
            return 0.0
        return self.story_points_completed / self.story_points_committed
