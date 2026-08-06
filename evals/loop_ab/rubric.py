# module-kind: code
"""Turn per-loop measurements into one comparable score.

Five dimensions: correctness (the brief's acceptance gates), tokens, wall-clock
latency, turn efficiency, and a resilience/rework composite. Correctness both
dominates the weighting and acts as a hard gate, because the output feeds a
promotion decision: no combination of cheap, fast and terse should outrank
actually solving the task.

Efficiency dimensions are unbounded and lower-is-better, so each is scored
relative to the best performer in the same ``(brief, tier)`` cell. That keeps
the composite comparable across briefs of very different sizes and invariant to
which provider was measured, which is what lets a token-ranked scoreboard stay
meaningful for an organisation spanning several providers.

Weights live here as named constants rather than in YAML, matching the
``EXEC_WEIGHT_*`` precedent in :mod:`evals.scoring.executable`; the emitted
scoreboard stamps them so every artifact is self-describing.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.scoring.executable import EXEC_TOTAL
from synthorg.core.types import NotBlankStr

#: Per-dimension contribution to the composite. Sums to :data:`RUBRIC_TOTAL`.
RUBRIC_WEIGHT_CORRECTNESS: Final[int] = 60
RUBRIC_WEIGHT_TOKENS: Final[int] = 15
RUBRIC_WEIGHT_LATENCY: Final[int] = 10
RUBRIC_WEIGHT_TURNS: Final[int] = 10
RUBRIC_WEIGHT_RESILIENCE: Final[int] = 5
RUBRIC_TOTAL: Final[int] = 100

#: Median correctness a loop must reach to stay eligible for promotion. Set to
#: the weight of the hidden-test class in :mod:`evals.scoring.executable`, so a
#: loop clears the gate exactly when it passes the brief's real acceptance
#: tests rather than only compiling and linting.
CORRECTNESS_GATE_FLOOR: Final[float] = 60.0

#: Resilience blends how often the loop succeeded at all with how much work it
#: had to redo. Pass rate weighs more: a loop that thrashes but lands is more
#: promotable than one that is tidy and flaky.
RESILIENCE_WEIGHT_PASS_RATE: Final[float] = 0.6
RESILIENCE_WEIGHT_REWORK: Final[float] = 0.4

#: Additive smoothing for the rework ratio. Zero rework is the expected good
#: case rather than a degenerate one, so the ratio is offset to keep a clean
#: run at 1.0 instead of collapsing every other loop to zero.
REWORK_SMOOTHING: Final[float] = 1.0


class LoopAggregate(BaseModel):
    """One loop's measurements for a single ``(brief, tier)`` cell.

    Values are already reduced across repetitions (median for the continuous
    measures, a true rate for ``pass_rate``), so this model is the boundary
    between "what we measured" and "how we score it".
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    loop_type: NotBlankStr
    correctness: float = Field(ge=0.0, le=float(EXEC_TOTAL))
    total_tokens: float = Field(ge=0.0)
    duration_seconds: float = Field(ge=0.0)
    total_turns: float = Field(ge=0.0)
    repeated_tool_calls: float = Field(ge=0.0)
    provider_retries: float | None = Field(default=None, ge=0.0)
    pass_rate: float = Field(ge=0.0, le=1.0)

    # ``@property`` rather than ``@computed_field`` for the reason
    # ``RunMetrics.total_tokens`` documents: this model round-trips through the
    # scoreboard JSON and a serialised derived value would trip
    # ``extra="forbid"`` on reparse.
    @property
    def rework_events(self) -> float:
        """Rework as measured, for reporting.

        Scoring does not use this: :func:`score_cell` decides per cell whether
        the retry component is comparable at all. Reading it as a score would
        put the unmeasurable loop back at zero rework.
        """
        return self.repeated_tool_calls + (self.provider_retries or 0.0)


class DimensionScores(BaseModel):
    """Per-dimension normalised scores in ``[0, 1]``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    correctness: float = Field(ge=0.0, le=1.0)
    tokens: float = Field(ge=0.0, le=1.0)
    latency: float = Field(ge=0.0, le=1.0)
    turns: float = Field(ge=0.0, le=1.0)
    resilience: float = Field(ge=0.0, le=1.0)


class LoopCellScore(BaseModel):
    """One loop's scored row for a ``(brief, tier)`` cell.

    Invariant: ``disqualified`` holds exactly when a reason is recorded. A
    disqualified row keeps its real dimension scores and composite; the flag
    reports that the loop is ineligible for promotion, it never rewrites the
    measurement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    loop_type: NotBlankStr
    dimensions: DimensionScores
    composite: float = Field(ge=0.0, le=float(RUBRIC_TOTAL))
    disqualified: bool
    disqualification_reason: str | None = None

    @model_validator(mode="after")
    def _reason_matches_flag(self) -> Self:
        """Enforce ``disqualified <=> disqualification_reason is not None``."""
        if self.disqualified != (self.disqualification_reason is not None):
            msg = (
                f"LoopCellScore {self.loop_type!r}: "
                f"disqualified={self.disqualified} does not match "
                f"disqualification_reason={self.disqualification_reason!r}"
            )
            raise ValueError(msg)
        return self


def _efficiency(observed: float, best: float) -> float:
    """Score a lower-is-better measurement against the cell's best.

    Returns:
        ``best / observed`` clamped to ``[0, 1]``.
    """
    if observed <= 0.0:
        # Spent nothing, so nothing can beat it. Doing nothing is punished by
        # correctness, which is where that failure actually belongs.
        return 1.0
    if best <= 0.0:
        return 0.0
    return min(best / observed, 1.0)


def _comparable_rework(aggregates: tuple[LoopAggregate, ...]) -> tuple[float, ...]:
    """Rework per loop, on a basis every loop in the cell can be measured on.

    Provider retries are only observable for a loop whose driver reports them;
    a loop retrying inside its own harness reports ``None``. Scoring that as
    zero would award it the cell's best rework ratio precisely because nothing
    watched it, so when any loop here cannot report retries the retry component
    is dropped for every loop and the ranking rests on repeated tool calls
    alone. Dropping it cell-wide rather than per loop is what keeps the
    comparison like for like.

    Returns:
        One rework figure per aggregate, in the order supplied.
    """
    retries_comparable = all(a.provider_retries is not None for a in aggregates)
    return tuple(
        a.repeated_tool_calls + (a.provider_retries or 0.0)
        if retries_comparable
        else a.repeated_tool_calls
        for a in aggregates
    )


def _resilience(
    aggregate: LoopAggregate, *, rework: float, best_rework: float
) -> float:
    """Blend pass rate with how much work the loop had to redo.

    Args:
        aggregate: The loop's reduced measurements.
        rework: This loop's rework on the cell's comparable basis, from
            :func:`_comparable_rework`.
        best_rework: The lowest such figure in the cell.

    Returns:
        The resilience score in ``[0, 1]``.
    """
    rework_ratio = (best_rework + REWORK_SMOOTHING) / (rework + REWORK_SMOOTHING)
    return (
        RESILIENCE_WEIGHT_PASS_RATE * aggregate.pass_rate
        + RESILIENCE_WEIGHT_REWORK * min(rework_ratio, 1.0)
    )


def _composite(dimensions: DimensionScores) -> float:
    """Weight the normalised dimensions into a ``0..RUBRIC_TOTAL`` score.

    Returns:
        The composite score.
    """
    return (
        dimensions.correctness * RUBRIC_WEIGHT_CORRECTNESS
        + dimensions.tokens * RUBRIC_WEIGHT_TOKENS
        + dimensions.latency * RUBRIC_WEIGHT_LATENCY
        + dimensions.turns * RUBRIC_WEIGHT_TURNS
        + dimensions.resilience * RUBRIC_WEIGHT_RESILIENCE
    )


def _validate_cell(aggregates: tuple[LoopAggregate, ...]) -> None:
    """Reject a cell the runner could not have produced legitimately.

    Raises:
        ValueError: The cell is empty, or a loop appears more than once.
    """
    if not aggregates:
        msg = (
            "score_cell requires at least one loop aggregate; "
            "an empty cell is not a result"
        )
        raise ValueError(msg)
    seen = [a.loop_type for a in aggregates]
    if len(set(seen)) != len(seen):
        msg = f"score_cell received duplicate loop types in one cell: {sorted(seen)}"
        raise ValueError(msg)


def score_cell(aggregates: tuple[LoopAggregate, ...]) -> tuple[LoopCellScore, ...]:
    """Score every loop in one ``(brief, tier)`` cell against each other.

    Args:
        aggregates: One entry per loop measured in this cell.

    Returns:
        A scored row per loop, in the order supplied.

    Raises:
        ValueError: The cell is empty or contains a duplicate loop type.
    """
    _validate_cell(aggregates)

    best_tokens = min(a.total_tokens for a in aggregates)
    best_duration = min(a.duration_seconds for a in aggregates)
    best_turns = min(a.total_turns for a in aggregates)
    rework = _comparable_rework(aggregates)
    best_rework = min(rework)

    scored: list[LoopCellScore] = []
    for aggregate, loop_rework in zip(aggregates, rework, strict=True):
        dimensions = DimensionScores(
            correctness=aggregate.correctness / EXEC_TOTAL,
            tokens=_efficiency(aggregate.total_tokens, best_tokens),
            latency=_efficiency(aggregate.duration_seconds, best_duration),
            turns=_efficiency(aggregate.total_turns, best_turns),
            resilience=_resilience(
                aggregate, rework=loop_rework, best_rework=best_rework
            ),
        )
        below_gate = aggregate.correctness < CORRECTNESS_GATE_FLOOR
        reason = (
            f"median correctness {aggregate.correctness:.1f} is below the "
            f"promotion gate floor {CORRECTNESS_GATE_FLOOR:.1f}"
            if below_gate
            else None
        )
        scored.append(
            LoopCellScore(
                loop_type=aggregate.loop_type,
                dimensions=dimensions,
                composite=_composite(dimensions),
                disqualified=below_gate,
                disqualification_reason=reason,
            )
        )
    return tuple(scored)


__all__ = [
    "CORRECTNESS_GATE_FLOOR",
    "RESILIENCE_WEIGHT_PASS_RATE",
    "RESILIENCE_WEIGHT_REWORK",
    "REWORK_SMOOTHING",
    "RUBRIC_TOTAL",
    "RUBRIC_WEIGHT_CORRECTNESS",
    "RUBRIC_WEIGHT_LATENCY",
    "RUBRIC_WEIGHT_RESILIENCE",
    "RUBRIC_WEIGHT_TOKENS",
    "RUBRIC_WEIGHT_TURNS",
    "DimensionScores",
    "LoopAggregate",
    "LoopCellScore",
    "score_cell",
]
