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
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.loop_ab.rubric import LoopAggregate
from evals.runner.metrics import RunMetrics
from evals.scoring.executable import EXEC_TOTAL
from synthorg.core.types import NotBlankStr


class RepetitionOutcome(BaseModel):
    """One recorded run of one loop against one brief.

    Attributes:
        correctness: The brief's grade over the workspace the run left behind.
        passed: Whether that grade was clean.
        termination_reason: How the loop ended, verbatim.
        artifacts_produced: Whether every file the brief declared exists in the
            graded tree. Measured where the answer is, on disk, rather than
            inferred from the loop's own account of itself.
        governance_events: Per-event counts of the process facts the run
            emitted (budget stops, turn ceilings, stagnation, approval rework).
        metrics: The per-run figures the rubric ranks on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    correctness: int = Field(ge=0, le=EXEC_TOTAL)
    passed: bool
    termination_reason: NotBlankStr
    artifacts_produced: bool
    governance_events: dict[str, int] = Field(default_factory=dict)
    metrics: RunMetrics

    @property
    def rework_events(self) -> float:
        """Total units of work this run had to redo, as measured.

        Provider retries and repeated tool calls are both a unit of work done
        more than once, so they count alike. An unobservable retry count
        contributes nothing here, which is why the rubric scores from the two
        components rather than from this sum: it cannot tell an unwatched loop
        from a clean one.
        """
        return float(
            (self.metrics.provider_retries or 0) + self.metrics.repeated_tool_calls
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
    """A loop's repetitions for one ``(brief, tier)`` cell, reduced for scoring.

    The three reporting fields sit outside :class:`LoopAggregate` on purpose:
    the aggregate is what the rubric scores, and these are what the operator
    reads. A loop that keeps ending NO_OP, or keeps tripping the turn ceiling,
    is already priced into the composite through correctness and turns, so
    scoring them again would weight one behaviour twice.

    Attributes:
        aggregate: The reduced figures the rubric ranks on.
        correctness_spread: Min / median / max grade across repetitions.
        repetitions: How many runs this summary reduces.
        repetitions_planned: How many the manifest asked for. Carried because
            a cell that lost its last repetition to a failure is otherwise
            indistinguishable from a manifest that only ever wanted the ones
            that ran, and the two are read very differently: the first is a
            weaker measurement of a loop that broke, the second is the whole
            measurement.
        termination_reasons: How many repetitions ended each way. A single pass
            rate cannot tell a silent no-op from an error from a turn ceiling,
            and which one a loop keeps hitting is the decision-relevant part.
        artifact_rate: Fraction of repetitions that left every declared file on
            disk.
        governance_events: Per-event totals across the repetitions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    aggregate: LoopAggregate
    correctness_spread: Spread
    repetitions: int = Field(gt=0)
    repetitions_planned: int = Field(gt=0)
    termination_reasons: dict[str, int] = Field(default_factory=dict)
    artifact_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    governance_events: dict[str, int] = Field(default_factory=dict)

    @property
    def is_partial(self) -> bool:
        """Whether a repetition the manifest asked for never completed.

        Returns:
            ``True`` when fewer runs were recorded than planned.
        """
        return self.repetitions < self.repetitions_planned

    @model_validator(mode="after")
    def _recorded_within_plan(self) -> Self:
        """Reject a summary claiming more runs than were ever asked for.

        Raises:
            ValueError: More repetitions were recorded than planned.
        """
        if self.repetitions > self.repetitions_planned:
            msg = (
                f"LoopRepetitionSummary reduces {self.repetitions} repetitions "
                f"but only {self.repetitions_planned} were planned"
            )
            raise ValueError(msg)
        return self


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


def _tally(values: Iterable[str]) -> dict[str, int]:
    """Count how often each value occurs, in first-seen order.

    Returns:
        A count per distinct value.
    """
    return dict(Counter(values))


def _sum_counts(mappings: Iterable[Mapping[str, int]]) -> dict[str, int]:
    """Add per-key counts across several mappings.

    Returns:
        The summed counts, in first-seen key order.
    """
    total: Counter[str] = Counter()
    for mapping in mappings:
        total.update(mapping)
    return dict(total)


def summarise_repetitions(
    *, loop_type: str, outcomes: tuple[RepetitionOutcome, ...], planned: int
) -> LoopRepetitionSummary:
    """Reduce *outcomes* into the rubric's per-loop aggregate plus its spread.

    Args:
        loop_type: The loop these repetitions measured.
        outcomes: Every recorded repetition for one ``(brief, tier)`` cell.
        planned: How many repetitions the manifest asked for, carried so a
            cell that lost one to a failure reads differently from a manifest
            that only wanted the ones that ran.

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
            repeated_tool_calls=statistics.median(
                float(o.metrics.repeated_tool_calls) for o in outcomes
            ),
            # Unobservable for the loop unless every repetition measured it:
            # a median mixing measured runs with unmeasured ones would report
            # a retry count for a loop that only sometimes had one taken.
            provider_retries=(
                statistics.median(
                    float(o.metrics.provider_retries or 0) for o in outcomes
                )
                if all(o.metrics.provider_retries is not None for o in outcomes)
                else None
            ),
            pass_rate=passed / len(outcomes),
        ),
        correctness_spread=_spread(correctness),
        repetitions=len(outcomes),
        repetitions_planned=planned,
        termination_reasons=_tally(o.termination_reason for o in outcomes),
        artifact_rate=sum(1 for o in outcomes if o.artifacts_produced) / len(outcomes),
        governance_events=_sum_counts(o.governance_events for o in outcomes),
    )


__all__ = [
    "LoopRepetitionSummary",
    "RepetitionOutcome",
    "Spread",
    "summarise_repetitions",
]
