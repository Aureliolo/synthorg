"""Data models for the Hybrid Plan + ReAct execution loop.

Defines the configuration model for the hybrid loop with per-step
turn limits, progress-summary checkpoints, and optional replanning.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.trajectory.models import TrajectoryConfig


class HybridLoopConfig(BaseModel):
    """Configuration for the Hybrid Plan + ReAct loop.

    Attributes:
        planner_model: Model override for plan generation and progress
            summaries.  ``None`` uses the agent's default model.
        executor_model: Model override for step execution.
            ``None`` uses the agent's default model.
        max_plan_steps: Upper limit on plan steps.  Plans exceeding
            this count are truncated with a warning.
        max_turns_per_step: Maximum LLM turns per mini-ReAct step.
            When exhausted, the step is marked as failed.
        max_replans: Maximum number of re-planning attempts (on step
            failure or LLM-decided replan).
        checkpoint_after_each_step: When ``True``, produce a progress
            summary via an LLM call after each completed step.
        allow_replan_on_completion: When ``True``, the progress summary
            can trigger replanning even on successful steps.  When
            ``False``, replanning only happens on step failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    planner_model: NotBlankStr | None = Field(
        default=None,
        description=(
            "Model override for plan generation and progress summaries "
            "(None = agent default)"
        ),
    )
    executor_model: NotBlankStr | None = Field(
        default=None,
        description=("Model override for step execution (None = agent default)"),
    )
    max_plan_steps: int = Field(
        default=7,
        ge=1,
        le=20,
        description="Upper limit on plan steps",
    )
    max_turns_per_step: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum LLM turns per mini-ReAct step",
    )
    max_replans: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum number of re-planning attempts",
    )
    checkpoint_after_each_step: bool = Field(
        default=True,
        description=("Produce a progress summary after each completed step"),
    )
    allow_replan_on_completion: bool = Field(
        default=True,
        description=(
            "Allow the progress summary to trigger replanning on successful steps"
        ),
    )
    trajectory: TrajectoryConfig | None = Field(
        default=None,
        description=("Best-of-K trajectory scoring configuration (None = disabled)"),
    )
