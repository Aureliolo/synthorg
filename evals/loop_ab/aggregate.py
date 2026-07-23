# module-kind: code
"""Reduce a loop's repeated runs into one scoreable aggregate.

Repetitions exist so a single unlucky run cannot flip a promotion decision, so
the continuous measures reduce by median rather than mean: one pathological run
moves a mean a long way and a median barely at all.

The spread is carried forward rather than collapsed. Two loops can share a
median while differing completely in consistency, and that difference is
decision-relevant, so the report shows it.
"""

import statistics
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.loop_ab.rubric import LoopAggregate
from evals.runner.metrics import RunMetrics
from evals.scoring.executable import EXEC_TOTAL
from synthorg.core.types import NotBlankStr


class RepetitionOutcome(BaseModel):
    """One recorded run of one loop against one brief."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    correctness: int = Field(ge=0, le=EXEC_TOTAL)
    passed: bool
    termination_reason: NotBlankStr
    metrics: RunMetrics

    @property
    def rework_events(self) -> float:
        """Total units of work this run had to redo.

        Provider retries, planner replans and repeated tool calls are all a
        unit of work done more than once, so they count alike. The rubric
        weights resilience lightly, so the blend matters far less than the
        signal being present and comparable across loops.
        """
        return float(
            self.metrics.provider_retries
            + self.metrics.replans_used
            + self.metrics.repeated_tool_calls
        )


class Spread(BaseModel):
    """Min / median / max of a measure across repetitions.

    Invariant: ``minimum <= median <= maximum``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    minimum: float
    median: float
    maximum: float

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> Self:
        """Reject a spread whose bounds do not bracket its median."""
        if not self.minimum <= self.median <= self.maximum:
            msg = (
                f"Spread bounds are unordered: minimum={self.minimum}, "
                f"median={self.median}, maximum={self.maximum}"
            )
            raise ValueError(msg)
        return self


class LoopRepetitionSummary(BaseModel):
    """A loop's repetitions for one ``(brief, tier)`` cell, reduced for scoring."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    aggregate: LoopAggregate
    correctness_spread: Spread
    repetitions: int = Field(gt=0)


def _spread(values: tuple[float, ...]) -> Spread:
    """Build the min / median / max spread of *values*.

    Returns:
        The :class:`Spread` over the supplied values.
    """
    return Spread(
        minimum=min(values),
        median=statistics.median(values),
        maximum=max(values),
    )


def summarise_repetitions(
    *, loop_type: str, outcomes: tuple[RepetitionOutcome, ...]
) -> LoopRepetitionSummary:
    """Reduce *outcomes* into the rubric's per-loop aggregate plus its spread.

    Args:
        loop_type: The loop these repetitions measured.
        outcomes: Every recorded repetition for one ``(brief, tier)`` cell.

    Returns:
        The reduced :class:`LoopRepetitionSummary`.

    Raises:
        ValueError: No repetitions were supplied. Zero recorded runs means the
            recording is broken, which must not read as a zero-scoring loop.
    """
    if not outcomes:
        msg = (
            f"loop {loop_type!r} has no recorded repetitions; at least one is "
            "required (an unrecorded loop is not a zero-scoring loop)"
        )
        raise ValueError(msg)

    correctness = tuple(float(o.correctness) for o in outcomes)
    passed = sum(1 for o in outcomes if o.passed)
    return LoopRepetitionSummary(
        aggregate=LoopAggregate(
            loop_type=NotBlankStr(loop_type),
            correctness=statistics.median(correctness),
            total_tokens=statistics.median(
                float(o.metrics.total_tokens) for o in outcomes
            ),
            duration_seconds=statistics.median(
                o.metrics.duration_seconds for o in outcomes
            ),
            total_turns=statistics.median(
                float(o.metrics.total_turns) for o in outcomes
            ),
            rework_events=statistics.median(o.rework_events for o in outcomes),
            pass_rate=passed / len(outcomes),
        ),
        correctness_spread=_spread(correctness),
        repetitions=len(outcomes),
    )


__all__ = [
    "LoopRepetitionSummary",
    "RepetitionOutcome",
    "Spread",
    "summarise_repetitions",
]
