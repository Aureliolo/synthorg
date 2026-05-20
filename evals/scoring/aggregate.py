"""Aggregate raw grade with process-fact penalties.

The aggregator is the single source of truth for "what is this brief's
final score", combining the grader's deterministic ``[0, 100]`` and
the runner's collected process-fact event counts via the penalty table.
"""

from typing import TYPE_CHECKING, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

if TYPE_CHECKING:
    from evals.scoring.penalties import PenaltyTable

# Brief grades are reported on a [0, 100] scale; the floor / ceiling
# clamps are enforced here so a misbehaving grader cannot push a brief
# into a domain the scorecard models do not anticipate.
GRADE_FLOOR: Final[int] = 0
GRADE_CEILING: Final[int] = 100


class PenaltyEntry(BaseModel):
    """One row in the per-brief penalty breakdown.

    Invariants:
      - ``raw_points == points_per_event * count``
      - ``applied_points <= raw_points`` (the cap may have clamped it)
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    event_constant: NotBlankStr
    count: int = Field(ge=0)
    points_per_event: int = Field(ge=0)
    raw_points: int = Field(ge=0)
    applied_points: int = Field(ge=0)

    @model_validator(mode="after")
    def _arithmetic_is_consistent(self) -> Self:
        """Enforce ``raw == points * count`` and ``applied <= raw`` at build time."""
        expected_raw = self.points_per_event * self.count
        if self.raw_points != expected_raw:
            msg = (
                f"PenaltyEntry {self.event_constant!r}: "
                f"raw_points={self.raw_points} does not match "
                f"points_per_event={self.points_per_event} * count={self.count}"
                f" (={expected_raw})"
            )
            raise ValueError(msg)
        if self.applied_points > self.raw_points:
            msg = (
                f"PenaltyEntry {self.event_constant!r}: "
                f"applied_points={self.applied_points} exceeds "
                f"raw_points={self.raw_points}"
            )
            raise ValueError(msg)
        return self


class AggregationResult(BaseModel):
    """The outcome of combining grade + process facts for one brief.

    Invariant: ``score == max(grade - deduction, floor)`` where ``floor``
    is whichever lower bound the aggregator was configured with (the
    penalty table's ``floor``, never the loose global ``GRADE_FLOOR``).
    The aggregator and validator share that one source so a custom
    ``PenaltyTable.floor`` does not desynchronise the two.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    grade: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    deduction: int = Field(ge=0)
    score: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    floor: int = Field(default=GRADE_FLOOR, ge=GRADE_FLOOR, le=GRADE_CEILING)
    entries: tuple[PenaltyEntry, ...]

    # ``@property`` (not ``@computed_field``) so the derived value never
    # lands in serialised JSON; ``model_validate_json`` would reparse the
    # value and trip ``extra="forbid"``. Same pattern as
    # :class:`evals.models.scorecard.ProcessFactReport.is_clean`.
    @property
    def is_clean(self) -> bool:
        """Whether no process-fact penalties were applied."""
        return self.deduction == 0

    @model_validator(mode="after")
    def _score_matches_grade_minus_deduction(self) -> Self:
        """Enforce ``score == max(grade - deduction, floor)`` at build time."""
        expected = max(self.grade - self.deduction, self.floor)
        if self.score != expected:
            msg = (
                f"AggregationResult: score={self.score} does not match "
                f"expected {expected} "
                f"(grade={self.grade} - deduction={self.deduction}, "
                f"floored at {self.floor})"
            )
            raise ValueError(msg)
        return self


def aggregate_brief_score(
    grade: int,
    events_by_class: dict[str, int],
    penalty_table: PenaltyTable,
) -> AggregationResult:
    """Combine a brief grade with collected process-fact events.

    Args:
        grade: Raw grade in ``[0, 100]`` produced by the grader (
            executable or judged). Values outside the range raise
            :class:`ValueError`; the grader is internal code, so an
            out-of-range value is a bug to surface rather than data to
            silently fix up.
        events_by_class: Map of event-constant strings to occurrence
            counts collected during the brief's run.
        penalty_table: Resolved penalty configuration; defaults at the
            top level of the scorer.

    Returns:
        :class:`AggregationResult` carrying the original grade, the
        total deduction (post-cap), the floored final score, and a
        breakdown of every tracked event class.

    Raises:
        ValueError: If *grade* is outside ``[0, 100]``.
    """
    if not GRADE_FLOOR <= grade <= GRADE_CEILING:
        msg = f"grade {grade} is outside [{GRADE_FLOOR}, {GRADE_CEILING}]"
        raise ValueError(msg)

    entries: list[PenaltyEntry] = []
    total_deduction = 0
    for event_constant, count in sorted(events_by_class.items()):
        if count < 0:
            msg = f"event count for {event_constant!r} must be >= 0 (got {count})"
            raise ValueError(msg)
        if not penalty_table.is_tracked(event_constant):
            continue
        per_event = penalty_table.points_for(event_constant)
        raw = per_event * count
        applied = min(raw, penalty_table.cap_per_class)
        total_deduction += applied
        entries.append(
            PenaltyEntry(
                event_constant=event_constant,
                count=count,
                points_per_event=per_event,
                raw_points=raw,
                applied_points=applied,
            )
        )

    final_score = max(penalty_table.floor, grade - total_deduction)
    return AggregationResult(
        grade=grade,
        deduction=total_deduction,
        score=final_score,
        floor=penalty_table.floor,
        entries=tuple(entries),
    )


__all__ = [
    "GRADE_CEILING",
    "GRADE_FLOOR",
    "AggregationResult",
    "PenaltyEntry",
    "aggregate_brief_score",
]
