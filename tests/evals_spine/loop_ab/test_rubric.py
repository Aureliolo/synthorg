# module-kind: tests
"""The rubric that turns per-loop measurements into one comparable score.

The issue this harness answers is a promotion decision, so two properties matter
more than any individual number: a strictly better loop must score strictly
higher, and a loop that does not actually solve the task must never win by being
cheap. Both are pinned here.

Efficiency dimensions are scored relative to the best performer in the same
``(brief, tier)`` cell, which keeps the composite comparable across briefs of
very different sizes and invariant to which provider was measured.
"""

import pytest

from evals.loop_ab.rubric import (
    CORRECTNESS_GATE_FLOOR,
    RESILIENCE_WEIGHT_PASS_RATE,
    RESILIENCE_WEIGHT_REWORK,
    RUBRIC_TOTAL,
    RUBRIC_WEIGHT_CORRECTNESS,
    RUBRIC_WEIGHT_LATENCY,
    RUBRIC_WEIGHT_RESILIENCE,
    RUBRIC_WEIGHT_TOKENS,
    RUBRIC_WEIGHT_TURNS,
    LoopAggregate,
    LoopCellScore,
    score_cell,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _aggregate(  # noqa: PLR0913 -- orthogonal per-loop measurements
    loop_type: str,
    *,
    correctness: float = 100.0,
    total_tokens: float = 1000.0,
    duration_seconds: float = 10.0,
    total_turns: float = 5.0,
    rework_events: float = 0.0,
    pass_rate: float = 1.0,
) -> LoopAggregate:
    """Build one loop's aggregate for a single (brief, tier) cell."""
    return LoopAggregate(
        loop_type=NotBlankStr(loop_type),
        correctness=correctness,
        total_tokens=total_tokens,
        duration_seconds=duration_seconds,
        total_turns=total_turns,
        rework_events=rework_events,
        pass_rate=pass_rate,
    )


def _by_loop(scores: tuple[LoopCellScore, ...]) -> dict[str, LoopCellScore]:
    """Index scored rows by loop type."""
    return {score.loop_type: score for score in scores}


def test_weights_sum_to_the_declared_total() -> None:
    """The composite is only interpretable as 0..100 if the weights sum to it."""
    assert (
        RUBRIC_WEIGHT_CORRECTNESS
        + RUBRIC_WEIGHT_TOKENS
        + RUBRIC_WEIGHT_LATENCY
        + RUBRIC_WEIGHT_TURNS
        + RUBRIC_WEIGHT_RESILIENCE
    ) == RUBRIC_TOTAL


def test_resilience_sub_weights_sum_to_one() -> None:
    """Resilience is a weighted blend, so its parts must form a full unit."""
    assert pytest.approx(1.0) == RESILIENCE_WEIGHT_PASS_RATE + RESILIENCE_WEIGHT_REWORK


def test_correctness_dominates_the_composite() -> None:
    """Correctness must outweigh every efficiency dimension combined.

    This is the ordering the promotion decision depends on: no combination of
    cheap, fast and terse can outrank actually solving the task.
    """
    efficiency = (
        RUBRIC_WEIGHT_TOKENS
        + RUBRIC_WEIGHT_LATENCY
        + RUBRIC_WEIGHT_TURNS
        + RUBRIC_WEIGHT_RESILIENCE
    )

    assert efficiency < RUBRIC_WEIGHT_CORRECTNESS


def test_a_strictly_better_loop_scores_strictly_higher() -> None:
    """The discriminating property the whole harness exists to provide."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate(
                    "react",
                    correctness=100.0,
                    total_tokens=1_000.0,
                    duration_seconds=10.0,
                    total_turns=4.0,
                    rework_events=0.0,
                ),
                _aggregate(
                    "hybrid",
                    correctness=80.0,
                    total_tokens=4_000.0,
                    duration_seconds=40.0,
                    total_turns=12.0,
                    rework_events=6.0,
                ),
            )
        )
    )

    assert scores["react"].composite > scores["hybrid"].composite


def test_a_dominant_loop_scores_full_marks() -> None:
    """Best on every dimension with a clean pass rate is the 100 case."""
    scores = _by_loop(
        score_cell((_aggregate("react"), _aggregate("hybrid", correctness=50.0)))
    )

    assert scores["react"].composite == pytest.approx(float(RUBRIC_TOTAL))


def test_the_best_performer_on_a_dimension_normalises_to_one() -> None:
    """Efficiency is relative, so the cheapest loop in the cell anchors at 1.0."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", total_tokens=500.0),
                _aggregate("hybrid", total_tokens=2_000.0),
            )
        )
    )

    assert scores["react"].dimensions.tokens == pytest.approx(1.0)
    assert scores["hybrid"].dimensions.tokens == pytest.approx(0.25)


def test_identical_loops_score_identically() -> None:
    """Scoring carries no ordering or naming bias."""
    scores = _by_loop(score_cell((_aggregate("react"), _aggregate("plan_execute"))))

    assert scores["react"].composite == pytest.approx(scores["plan_execute"].composite)


def test_a_cheap_loop_that_fails_the_task_is_disqualified() -> None:
    """The gate is the point: winning on price while failing is not a win."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate(
                    "react",
                    correctness=CORRECTNESS_GATE_FLOOR - 1.0,
                    total_tokens=1.0,
                    duration_seconds=0.1,
                    total_turns=1.0,
                ),
                _aggregate("hybrid", correctness=100.0, total_tokens=10_000.0),
            )
        )
    )

    assert scores["react"].disqualified is True
    assert scores["hybrid"].disqualified is False


def test_a_disqualified_loop_still_reports_its_real_numbers() -> None:
    """Disqualification is reported, never hidden by zeroing the row."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", correctness=0.0, total_tokens=100.0),
                _aggregate("hybrid", correctness=100.0, total_tokens=100.0),
            )
        )
    )
    react = scores["react"]

    assert react.disqualified is True
    assert react.disqualification_reason is not None
    assert react.dimensions.tokens == pytest.approx(1.0)
    assert react.composite > 0.0


def test_a_loop_exactly_on_the_gate_floor_is_not_disqualified() -> None:
    """The floor is inclusive, so a brief tuned to it does not flip on rounding."""
    scores = _by_loop(
        score_cell((_aggregate("react", correctness=CORRECTNESS_GATE_FLOOR),))
    )

    assert scores["react"].disqualified is False


def test_rework_lowers_resilience_relative_to_a_clean_run() -> None:
    """Redoing work is a cost, so a thrashing loop scores below a clean one."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", rework_events=0.0),
                _aggregate("hybrid", rework_events=10.0),
            )
        )
    )

    assert (
        scores["react"].dimensions.resilience > scores["hybrid"].dimensions.resilience
    )


def test_zero_rework_everywhere_does_not_zero_the_dimension() -> None:
    """Zero rework is the expected good case, not a degenerate one."""
    scores = _by_loop(
        score_cell((_aggregate("react", rework_events=0.0), _aggregate("hybrid")))
    )

    assert scores["react"].dimensions.resilience == pytest.approx(1.0)


def test_a_flaky_loop_scores_below_a_reliable_one() -> None:
    """Pass rate across repetitions is half the resilience story."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", pass_rate=1.0),
                _aggregate("hybrid", pass_rate=1.0 / 3.0),
            )
        )
    )

    assert (
        scores["react"].dimensions.resilience > scores["hybrid"].dimensions.resilience
    )


def test_a_single_loop_cell_anchors_every_efficiency_dimension() -> None:
    """With nothing to compare against, a loop is trivially its own best."""
    scores = _by_loop(score_cell((_aggregate("openhands"),)))
    dimensions = scores["openhands"].dimensions

    assert dimensions.tokens == pytest.approx(1.0)
    assert dimensions.latency == pytest.approx(1.0)
    assert dimensions.turns == pytest.approx(1.0)


def test_scoring_an_empty_cell_is_refused() -> None:
    """An empty cell means the runner recorded nothing; that is not a result."""
    with pytest.raises(ValueError, match="at least one"):
        score_cell(())


def test_duplicate_loops_in_a_cell_are_refused() -> None:
    """One row per loop per cell; a duplicate means the runner double-counted."""
    with pytest.raises(ValueError, match="duplicate"):
        score_cell((_aggregate("react"), _aggregate("react")))
