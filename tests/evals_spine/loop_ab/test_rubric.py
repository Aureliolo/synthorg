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


def _aggregate(
    loop_type: str,
    *,
    correctness: float = 100.0,
    total_tokens: float = 1000.0,
    duration_seconds: float = 10.0,
    total_turns: float = 5.0,
    repeated_tool_calls: float = 0.0,
    provider_retries: float | None = 0.0,
    pass_rate: float = 1.0,
) -> LoopAggregate:
    """Build one loop's aggregate for a single (brief, tier) cell."""
    return LoopAggregate(
        loop_type=NotBlankStr(loop_type),
        correctness=correctness,
        total_tokens=total_tokens,
        duration_seconds=duration_seconds,
        total_turns=total_turns,
        repeated_tool_calls=repeated_tool_calls,
        provider_retries=provider_retries,
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
    """No amount of cheap, fast and terse can outrank solving the task.

    Asserted on scores rather than on the weights: correctness taking 60 of
    100 makes the arithmetic hold for any weighting whatsoever, so a
    weight-only assertion would pass under a rubric that had lost this
    property entirely. Here a loop that solves the brief while being worst on
    every other axis still beats one that fails it while being best on all of
    them.
    """
    solved_but_wasteful = _aggregate(
        "solved",
        correctness=100.0,
        total_tokens=100_000.0,
        duration_seconds=600.0,
        total_turns=40.0,
        repeated_tool_calls=20.0,
        pass_rate=1.0,
    )
    failed_but_frugal = _aggregate(
        "failed",
        correctness=0.0,
        total_tokens=100.0,
        duration_seconds=1.0,
        total_turns=1.0,
        repeated_tool_calls=0.0,
        pass_rate=1.0,
    )

    scored = _by_loop(score_cell((solved_but_wasteful, failed_but_frugal)))

    assert scored["solved"].composite > scored["failed"].composite


def test_one_failure_in_three_costs_more_than_a_whole_cheap_dimension() -> None:
    """A run that gives up must not be able to buy back its loss by being cheap.

    A repetition that delivers nothing is cheap on every efficiency axis, so
    folding it into a cell's medians pulls tokens, wall clock and turns down
    together. Reliability therefore has to be worth more than the axes that
    reward the giving up: one failure in three must cost more than the latency
    and turn dimensions are worth in full.
    """
    cost_of_one_failure_in_three = (
        RESILIENCE_WEIGHT_PASS_RATE * (1 / 3) * RUBRIC_WEIGHT_RESILIENCE
    )

    assert cost_of_one_failure_in_three > RUBRIC_WEIGHT_LATENCY
    assert cost_of_one_failure_in_three > RUBRIC_WEIGHT_TURNS


def test_reliability_outweighs_any_single_efficiency_dimension() -> None:
    """Being cheapest on one axis must never substitute for landing the work."""
    assert RUBRIC_WEIGHT_RESILIENCE > RUBRIC_WEIGHT_TOKENS
    assert RUBRIC_WEIGHT_RESILIENCE > RUBRIC_WEIGHT_LATENCY
    assert RUBRIC_WEIGHT_RESILIENCE > RUBRIC_WEIGHT_TURNS


def test_a_cheap_failure_never_raises_a_loop_that_is_already_cheapest() -> None:
    """A run that gave up early is cheaper, and must not be rewarded for it.

    Both loops here deliver identical work; one simply failed a repetition,
    which made its medians the lowest in the cell. Efficiency is scored against
    the cell's best, so being cheapest caps at 1.0 and buys nothing further,
    while the failure is paid for in full.
    """
    reliable = _aggregate("reliable", pass_rate=1.0, total_tokens=68_000.0)
    gave_up = _aggregate("gave-up", pass_rate=2 / 3, total_tokens=34_000.0)

    scored = _by_loop(score_cell((reliable, gave_up)))

    assert scored["reliable"].composite > scored["gave-up"].composite


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
                    repeated_tool_calls=0.0,
                ),
                _aggregate(
                    "openhands",
                    correctness=80.0,
                    total_tokens=4_000.0,
                    duration_seconds=40.0,
                    total_turns=12.0,
                    repeated_tool_calls=6.0,
                ),
            )
        )
    )

    assert scores["react"].composite > scores["openhands"].composite


def test_a_dominant_loop_scores_full_marks() -> None:
    """Best on every dimension with a clean pass rate is the 100 case."""
    scores = _by_loop(
        score_cell((_aggregate("react"), _aggregate("openhands", correctness=50.0)))
    )

    assert scores["react"].composite == pytest.approx(float(RUBRIC_TOTAL))


def test_the_best_performer_on_a_dimension_normalises_to_one() -> None:
    """Efficiency is relative, so the cheapest loop in the cell anchors at 1.0."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", total_tokens=500.0),
                _aggregate("openhands", total_tokens=2_000.0),
            )
        )
    )

    assert scores["react"].dimensions.tokens == pytest.approx(1.0)
    assert scores["openhands"].dimensions.tokens == pytest.approx(0.25)


def test_identical_loops_score_identically() -> None:
    """Scoring carries no ordering or naming bias."""
    scores = _by_loop(score_cell((_aggregate("react"), _aggregate("openhands"))))

    assert scores["react"].composite == pytest.approx(scores["openhands"].composite)


def test_a_loop_that_spent_nothing_is_not_scored_as_infinitely_efficient() -> None:
    """A zero measurement is a division by zero, and it does happen.

    A run that died before its first turn reports zero tokens, zero seconds
    and zero turns. Efficiency is relative to the cell's best, so the honest
    answer is that nothing beat it, and correctness is where the failure is
    actually paid for.
    """
    scores = _by_loop(
        score_cell(
            (
                _aggregate(
                    "died",
                    correctness=0.0,
                    total_tokens=0.0,
                    duration_seconds=0.0,
                    total_turns=0.0,
                    pass_rate=0.0,
                ),
                _aggregate("worked", total_tokens=5_000.0),
            )
        )
    )
    died = scores["died"]

    assert died.dimensions.tokens == pytest.approx(1.0)
    assert died.dimensions.latency == pytest.approx(1.0)
    assert died.dimensions.turns == pytest.approx(1.0)
    assert died.disqualified is True
    assert scores["worked"].composite > died.composite


def test_a_cell_where_the_best_is_zero_scores_the_others_at_the_floor() -> None:
    """``best / observed`` has no meaningful value when the best spent nothing.

    Scoring the loops that did work against a zero baseline would rank them by
    an arbitrary ratio, so they take the floor on that dimension and the
    ranking rests on the dimensions that still mean something.
    """
    scores = _by_loop(
        score_cell(
            (
                _aggregate("died", correctness=0.0, total_tokens=0.0, pass_rate=0.0),
                _aggregate("worked", total_tokens=5_000.0),
            )
        )
    )

    assert scores["worked"].dimensions.tokens == pytest.approx(0.0)


def test_a_loop_that_passed_nothing_scores_no_pass_rate_component() -> None:
    """Resilience is pass rate plus rework, and the first can be zero."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("never-passed", pass_rate=0.0, repeated_tool_calls=0.0),
                _aggregate("always-passed", pass_rate=1.0, repeated_tool_calls=0.0),
            )
        )
    )

    # Rework is identical and best, so the whole gap is the pass-rate half.
    assert scores["never-passed"].dimensions.resilience == pytest.approx(
        RESILIENCE_WEIGHT_REWORK
    )
    assert scores["always-passed"].dimensions.resilience == pytest.approx(1.0)


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
                _aggregate("openhands", correctness=100.0, total_tokens=10_000.0),
            )
        )
    )

    assert scores["react"].disqualified is True
    assert scores["openhands"].disqualified is False


def test_a_disqualified_loop_still_reports_its_real_numbers() -> None:
    """Disqualification is reported, never hidden by zeroing the row."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", correctness=0.0, total_tokens=100.0),
                _aggregate("openhands", correctness=100.0, total_tokens=100.0),
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
                _aggregate("react", repeated_tool_calls=0.0),
                _aggregate("openhands", repeated_tool_calls=10.0),
            )
        )
    )

    assert (
        scores["react"].dimensions.resilience
        > scores["openhands"].dimensions.resilience
    )


def test_zero_rework_everywhere_does_not_zero_the_dimension() -> None:
    """Zero rework is the expected good case, not a degenerate one."""
    scores = _by_loop(
        score_cell(
            (_aggregate("react", repeated_tool_calls=0.0), _aggregate("openhands"))
        )
    )

    assert scores["react"].dimensions.resilience == pytest.approx(1.0)


def test_unobservable_retries_do_not_buy_the_best_rework_score() -> None:
    """A loop nothing watched must not out-score one that reported honestly."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", repeated_tool_calls=4.0, provider_retries=4.0),
                _aggregate("openhands", repeated_tool_calls=4.0, provider_retries=None),
            )
        )
    )

    assert scores["openhands"].dimensions.resilience == pytest.approx(
        scores["react"].dimensions.resilience
    )


def test_dropping_the_retry_submetric_keeps_the_rest_of_the_comparison() -> None:
    """Retries go, repeated tool calls stay: the cell still discriminates."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", repeated_tool_calls=0.0, provider_retries=9.0),
                _aggregate("openhands", repeated_tool_calls=8.0, provider_retries=None),
            )
        )
    )

    assert (
        scores["react"].dimensions.resilience
        > scores["openhands"].dimensions.resilience
    )


def test_retries_still_count_when_every_loop_reports_them() -> None:
    """Dropping the submetric is the exception, not the resting behaviour."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", repeated_tool_calls=0.0, provider_retries=0.0),
                _aggregate("openhands", repeated_tool_calls=0.0, provider_retries=9.0),
            )
        )
    )

    assert (
        scores["react"].dimensions.resilience
        > scores["openhands"].dimensions.resilience
    )


def test_a_flaky_loop_scores_below_a_reliable_one() -> None:
    """Pass rate across repetitions is half the resilience story."""
    scores = _by_loop(
        score_cell(
            (
                _aggregate("react", pass_rate=1.0),
                _aggregate("openhands", pass_rate=1.0 / 3.0),
            )
        )
    )

    assert (
        scores["react"].dimensions.resilience
        > scores["openhands"].dimensions.resilience
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
