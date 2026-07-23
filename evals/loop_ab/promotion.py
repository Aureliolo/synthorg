# module-kind: code
"""Derive a promotion recommendation from the measured scoreboard.

The harness introduces no selection machinery. Its entire output is a pair of
values for settings that already exist: ``engine.default_loop_type`` (the
fallback when no complexity rule matches) and ``engine.loop_complexity_overrides``
(per-complexity routing). An operator pastes them in; nothing else changes.

Two rules keep the recommendation honest:

* A loop that failed the correctness gate can never be recommended, however
  cheap or fast it was.
* An override is emitted only where the bucket's winner differs from the
  default, so the setting stays the minimal expression of the evidence.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.loop_ab.rubric import LoopCellScore
from synthorg.core.task_enums import Complexity
from synthorg.core.types import NotBlankStr

#: Complexity order used for the emitted override string. Fixed so a
#: re-recording produces a diffable recommendation rather than a reshuffle.
_COMPLEXITY_ORDER: tuple[Complexity, ...] = (
    Complexity.SIMPLE,
    Complexity.MEDIUM,
    Complexity.COMPLEX,
    Complexity.EPIC,
)


class ComplexityWinner(BaseModel):
    """The best promotable loop for one complexity bucket."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    complexity: Complexity
    loop_type: NotBlankStr
    composite: float = Field(ge=0.0)


class PromotionRecommendation(BaseModel):
    """Settings values the measured scoreboard supports.

    Invariant: a recommendation with no winners carries no default loop, and a
    recommendation with winners carries one. There is no half-decided state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    default_loop_type: str | None
    loop_complexity_overrides: str
    winners: tuple[ComplexityWinner, ...]

    @model_validator(mode="after")
    def _default_matches_winners(self) -> Self:
        """Reject a recommendation that names a default without evidence."""
        if bool(self.winners) != (self.default_loop_type is not None):
            msg = (
                "PromotionRecommendation: default_loop_type="
                f"{self.default_loop_type!r} does not match "
                f"{len(self.winners)} measured winner(s)"
            )
            raise ValueError(msg)
        if not self.winners and self.loop_complexity_overrides:
            msg = (
                "PromotionRecommendation: overrides "
                f"{self.loop_complexity_overrides!r} recommended with no winners"
            )
            raise ValueError(msg)
        return self


def recommend_promotion(
    cells: dict[Complexity, tuple[LoopCellScore, ...]],
) -> PromotionRecommendation:
    """Recommend settings values from the scored complexity buckets.

    Args:
        cells: Scored rows per complexity bucket, as measured.

    Returns:
        The :class:`PromotionRecommendation` the evidence supports. When no
        loop cleared the correctness gate anywhere, the recommendation is
        empty rather than a least-bad guess.

    Raises:
        ValueError: No buckets were supplied at all.
    """
    if not cells:
        msg = (
            "recommend_promotion requires at least one scored complexity "
            "bucket; there is nothing to recommend from"
        )
        raise ValueError(msg)

    winners: list[ComplexityWinner] = []
    for complexity in _COMPLEXITY_ORDER:
        scores = cells.get(complexity, ())
        promotable = [score for score in scores if not score.disqualified]
        if not promotable:
            continue
        best = max(promotable, key=lambda score: score.composite)
        winners.append(
            ComplexityWinner(
                complexity=complexity,
                loop_type=best.loop_type,
                composite=best.composite,
            )
        )

    if not winners:
        return PromotionRecommendation(
            default_loop_type=None, loop_complexity_overrides="", winners=()
        )

    default_loop = _most_frequent_winner(tuple(winners))
    overrides = ",".join(
        f"{winner.complexity.value}:{winner.loop_type}"
        for winner in winners
        if winner.loop_type != default_loop
    )
    return PromotionRecommendation(
        default_loop_type=default_loop,
        loop_complexity_overrides=overrides,
        winners=tuple(winners),
    )


def _most_frequent_winner(winners: tuple[ComplexityWinner, ...]) -> str:
    """Pick the loop that wins the most buckets, breaking ties on total score.

    Returns:
        The loop type to recommend as ``default_loop_type``.
    """
    tally: dict[str, tuple[int, float]] = {}
    for winner in winners:
        wins, total = tally.get(winner.loop_type, (0, 0.0))
        tally[winner.loop_type] = (wins + 1, total + winner.composite)
    # Sort by wins then aggregate score, and finally by name so a genuine tie
    # resolves to a stable value across re-recordings rather than dict order.
    return min(tally.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))[0]


__all__ = [
    "ComplexityWinner",
    "PromotionRecommendation",
    "recommend_promotion",
]
