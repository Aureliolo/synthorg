"""Step-level quality signal models.

Frozen Pydantic models for ternary step classification and
accuracy-effort ratio computation.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class StepQuality(StrEnum):
    """Ternary step quality classification.

    Based on AgentProcessBench step-level labeling:
    correct / neutral-exploratory / incorrect.
    """

    CORRECT = "correct"
    NEUTRAL = "neutral"
    INCORRECT = "incorrect"


class StepQualitySignal(BaseModel):
    """Quality signal for a single execution step.

    Attributes:
        quality: Ternary classification of the step outcome.
        confidence: Classifier confidence in the label (0.0--1.0).
        reason: Human-readable explanation of the classification.
        step_index: Zero-based index of the step in the plan.
        turn_range: Inclusive (start, end) turn numbers for this step.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    quality: StepQuality = Field(description="Ternary step classification")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classifier confidence (0.0--1.0)",
    )
    reason: NotBlankStr = Field(description="Human-readable classification reason")
    step_index: int = Field(ge=0, description="Zero-based step index")
    turn_range: tuple[int, int] = Field(
        description="Inclusive (start, end) turn numbers",
    )

    @model_validator(mode="after")
    def _validate_turn_range(self) -> Self:
        start, end = self.turn_range
        if start < 1:
            msg = f"turn_range start must be >= 1, got {start}"
            raise ValueError(msg)
        if end < start:
            msg = f"turn_range end ({end}) must be >= start ({start})"
            raise ValueError(msg)
        return self


class AccuracyEffortRatio(BaseModel):
    """Accuracy-effort trade-off metric for a task execution.

    Measures outcome quality relative to steps consumed, inspired
    by the MADQA benchmark's accuracy-effort trade-off metric.

    Attributes:
        accuracy: Fraction of correct steps (0.0--1.0).
        effort: Normalized step count (total / expected).
        ratio: Accuracy divided by effort (higher is better).
        correct_steps: Count of CORRECT steps.
        neutral_steps: Count of NEUTRAL steps.
        incorrect_steps: Count of INCORRECT steps.
        total_steps: Total step count.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    effort: float = Field(
        gt=0.0,
        description="Normalized step count (total / expected)",
    )
    correct_steps: int = Field(ge=0, description="Count of CORRECT steps")
    neutral_steps: int = Field(ge=0, description="Count of NEUTRAL steps")
    incorrect_steps: int = Field(ge=0, description="Count of INCORRECT steps")
    total_steps: int = Field(gt=0, description="Total step count")

    @computed_field(  # type: ignore[prop-decorator]
        description="Fraction of correct steps (0.0--1.0)",
    )
    @property
    def accuracy(self) -> float:
        """Fraction of correct steps."""
        return self.correct_steps / self.total_steps

    @computed_field(  # type: ignore[prop-decorator]
        description="Accuracy / effort ratio (higher is better)",
    )
    @property
    def ratio(self) -> float:
        """Accuracy divided by effort."""
        return self.accuracy / self.effort

    @model_validator(mode="after")
    def _validate_step_counts(self) -> Self:
        total = self.correct_steps + self.neutral_steps + self.incorrect_steps
        if total != self.total_steps:
            msg = (
                f"Step counts ({self.correct_steps} + {self.neutral_steps} "
                f"+ {self.incorrect_steps} = {total}) do not sum to "
                f"total_steps ({self.total_steps})"
            )
            raise ValueError(msg)
        return self
