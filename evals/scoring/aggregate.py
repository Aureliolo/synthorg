"""Aggregate raw grade with process-fact penalties.

The aggregator is the single source of truth for "what is this brief's
final score", combining the grader's deterministic ``[0, 100]`` and
the runner's collected process-fact event counts via the penalty table.
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from evals.scoring.penalties import PenaltyTable

# Brief grades are reported on a [0, 100] scale; the floor / ceiling
# clamps are enforced here so a misbehaving grader cannot push a brief
# into a domain the scorecard models do not anticipate.
GRADE_FLOOR: Final[int] = 0
GRADE_CEILING: Final[int] = 100


class PenaltyEntry(BaseModel):
    """One row in the per-brief penalty breakdown."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    event_constant: str
    count: int = Field(ge=0)
    points_per_event: int = Field(ge=0)
    raw_points: int = Field(ge=0)
    applied_points: int = Field(ge=0)


class AggregationResult(BaseModel):
    """The outcome of combining grade + process facts for one brief."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    grade: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    deduction: int = Field(ge=0)
    score: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    entries: tuple[PenaltyEntry, ...]

    @property
    def is_clean(self) -> bool:
        """Whether no process-fact penalties were applied."""
        return self.deduction == 0


def aggregate_brief_score(
    grade: int,
    events_by_class: dict[str, int],
    penalty_table: PenaltyTable,
) -> AggregationResult:
    """Combine a brief grade with collected process-fact events.

    Args:
        grade: Raw grade in ``[0, 100]`` produced by the grader (
            executable or judged); values outside the range are clamped
            and a ValueError is raised because the grader is internal,
            never untrusted input.
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
        entries=tuple(entries),
    )


__all__ = [
    "GRADE_CEILING",
    "GRADE_FLOOR",
    "AggregationResult",
    "PenaltyEntry",
    "aggregate_brief_score",
]
