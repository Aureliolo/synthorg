"""Data models for the Plan-and-Execute execution loop.

Defines the plan structure (steps, status, revisions) and the
configuration model for the plan-execute loop.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class StepStatus(StrEnum):
    """Execution status of a plan step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    """A single step within an execution plan.

    Attributes:
        step_number: 1-indexed position in the plan.
        description: What this step should accomplish.
        expected_outcome: The anticipated result of this step.
        status: Current execution status of this step.
        actual_outcome: Observed result after execution (if any).
    """

    model_config = ConfigDict(frozen=True)

    step_number: int = Field(gt=0, description="1-indexed step number")
    description: NotBlankStr = Field(description="Step description")
    expected_outcome: NotBlankStr = Field(
        description="Anticipated result of this step",
    )
    status: StepStatus = Field(
        default=StepStatus.PENDING,
        description="Current execution status",
    )
    actual_outcome: NotBlankStr | None = Field(
        default=None,
        description="Observed result after execution",
    )


class ExecutionPlan(BaseModel):
    """An ordered sequence of plan steps for task execution.

    Attributes:
        steps: Ordered tuple of plan steps.
        revision_number: Plan revision counter (0 = original).
        original_task_summary: Brief summary of the task being planned.
    """

    model_config = ConfigDict(frozen=True)

    steps: tuple[PlanStep, ...] = Field(
        min_length=1,
        description="Ordered plan steps",
    )
    revision_number: int = Field(
        default=0,
        ge=0,
        description="Plan revision counter (0 = original)",
    )
    original_task_summary: NotBlankStr = Field(
        description="Brief summary of the task being planned",
    )

    @model_validator(mode="after")
    def _validate_sequential_step_numbers(self) -> Self:
        """Ensure step numbers are sequential starting from 1."""
        expected = tuple(range(1, len(self.steps) + 1))
        actual = tuple(s.step_number for s in self.steps)
        if actual != expected:
            msg = (
                f"Step numbers must be sequential from 1: "
                f"expected {expected}, got {actual}"
            )
            raise ValueError(msg)
        return self


class PlanExecuteConfig(BaseModel):
    """Configuration for the Plan-and-Execute loop.

    Attributes:
        planner_model: Model override for plan generation.
            ``None`` uses the agent's default model.
        executor_model: Model override for step execution.
            ``None`` uses the agent's default model.
        max_replans: Maximum number of re-planning attempts on
            step failure.
    """

    model_config = ConfigDict(frozen=True)

    planner_model: NotBlankStr | None = Field(
        default=None,
        description="Model override for plan generation (None = agent default)",
    )
    executor_model: NotBlankStr | None = Field(
        default=None,
        description="Model override for step execution (None = agent default)",
    )
    max_replans: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum number of re-planning attempts",
    )
