# module-kind: tests
"""The headline axis: what a solved requirement cost, and how sure that is.

The two decisions that make the figure honest are asserted on hand-built runs
rather than left to a sweep: what the denominator is, and when an interval is
reported at all.
"""

import pytest

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.efficiency import (
    indistinguishable_depths,
    tokens_per_solved_by_achieved_depth,
    tokens_per_solved_by_depth_cap,
)
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import (
    LEAF,
    MIN_CELLS_FOR_INTERVAL,
    CellRecord,
    TokensPerSolvedPoint,
    UnitRecord,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _cell(
    *,
    tokens: int,
    passing: tuple[str, ...],
    arm: Arm = Arm.GATED,
    cap: int = 2,
    achieved: int = 2,
    repetition: int = 0,
) -> CellRecord:
    """One measured run that spent *tokens* and satisfied *passing*.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=cap,
        arm=arm,
        repetition=repetition,
        achieved_depth=achieved,
        units=(
            UnitRecord(
                unit_id=NotBlankStr(f"u-{arm.value}-{repetition}"),
                title=NotBlankStr("build it"),
                kind=LEAF,
                depth=1,
                delivered=True,
                attempts=1,
                tokens=tokens,
            ),
        ),
        merged_passing=tuple(RequirementId(item) for item in passing),
    )


def _repeated(
    count: int, *, tokens: int, passing: tuple[str, ...], arm: Arm = Arm.GATED
) -> tuple[CellRecord, ...]:
    """*count* identical runs of one bucket.

    Returns:
        The cells.
    """
    return tuple(
        _cell(tokens=tokens, passing=passing, arm=arm, repetition=index)
        for index in range(count)
    )


class TestWhatTheRatioIs:
    """Pooled over the bucket, like every other bucket figure."""

    def test_tokens_over_distinct_solved_requirements(self) -> None:
        cell = _cell(tokens=3000, passing=("R01", "R02", "R02"))

        point = tokens_per_solved_by_achieved_depth((cell,))[0]

        assert point.tokens == 3000
        assert point.solved == 2
        assert point.tokens_per_solved == pytest.approx(1500.0)

    def test_two_runs_pool_rather_than_average(self) -> None:
        # 1000/1 and 5000/3 average to 1333; pooled they are 6000/4 = 1500,
        # and pooled is the rule every other bucket figure follows.
        cells = (
            _cell(tokens=1000, passing=("R01",)),
            _cell(tokens=5000, passing=("R01", "R02", "R03"), repetition=1),
        )

        point = tokens_per_solved_by_achieved_depth(cells)[0]

        assert point.tokens_per_solved == pytest.approx(1500.0)

    def test_a_bucket_that_solved_nothing_has_no_finite_cost(self) -> None:
        cell = _cell(tokens=4000, passing=())

        point = tokens_per_solved_by_achieved_depth((cell,))[0]

        assert point.solved == 0
        assert point.tokens_per_solved is None

    def test_the_cap_curve_bins_on_the_cap(self) -> None:
        cell = _cell(tokens=1000, passing=("R01",), cap=4, achieved=2)

        assert tokens_per_solved_by_depth_cap((cell,))[0].depth == 4
        assert tokens_per_solved_by_achieved_depth((cell,))[0].depth == 2

    def test_an_unavailable_cell_contributes_nothing(self) -> None:
        unavailable = CellRecord(
            depth_cap=2, arm=Arm.GATED, repetition=0, unavailable_reason="gone"
        )

        assert tokens_per_solved_by_achieved_depth((unavailable,)) == ()


class TestWhenAnIntervalIsReported:
    """Only where the runs can support one, and then reproducibly."""

    def test_too_few_runs_report_the_point_and_no_interval(self) -> None:
        cells = _repeated(MIN_CELLS_FOR_INTERVAL - 1, tokens=1000, passing=("R01",))

        point = tokens_per_solved_by_achieved_depth(cells)[0]

        assert point.tokens_per_solved == pytest.approx(1000.0)
        assert point.ci_low is None
        assert point.ci_high is None
        assert point.unbounded_above is False

    def test_identical_runs_give_a_degenerate_interval_at_the_point(self) -> None:
        cells = _repeated(MIN_CELLS_FOR_INTERVAL, tokens=1000, passing=("R01",))

        point = tokens_per_solved_by_achieved_depth(cells)[0]

        assert point.ci_low == pytest.approx(1000.0)
        assert point.ci_high == pytest.approx(1000.0)

    def test_disagreeing_runs_give_an_interval_around_the_point(self) -> None:
        cells = (
            _cell(tokens=1000, passing=("R01",)),
            _cell(tokens=2000, passing=("R01",), repetition=1),
            _cell(tokens=4000, passing=("R01",), repetition=2),
            _cell(tokens=8000, passing=("R01",), repetition=3),
            _cell(tokens=1000, passing=("R01",), repetition=4),
        )

        point = tokens_per_solved_by_achieved_depth(cells)[0]

        assert point.ci_low is not None
        assert point.ci_high is not None
        assert point.tokens_per_solved is not None
        assert point.ci_low < point.ci_high
        assert point.ci_low <= point.tokens_per_solved <= point.ci_high

    def test_the_interval_is_a_function_of_the_runs_alone(self) -> None:
        """Two scorings of one journal publish one interval."""
        cells = (
            _cell(tokens=1000, passing=("R01",)),
            _cell(tokens=3000, passing=("R01", "R02"), repetition=1),
            _cell(tokens=9000, passing=("R01",), repetition=2),
        )

        first = tokens_per_solved_by_achieved_depth(cells)[0]
        second = tokens_per_solved_by_achieved_depth(tuple(reversed(cells)))[0]

        assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)

    def test_runs_that_sometimes_solve_nothing_leave_the_top_open(self) -> None:
        """A resample that solved nothing has no ceiling, and that is said."""
        cells = (
            _cell(tokens=1000, passing=("R01",)),
            _cell(tokens=1000, passing=("R01",), repetition=1),
            _cell(tokens=1000, passing=(), repetition=2),
            _cell(tokens=1000, passing=(), repetition=3),
        )

        point = tokens_per_solved_by_achieved_depth(cells)[0]

        assert point.ci_low is not None
        assert point.ci_high is None
        assert point.unbounded_above is True

    def test_a_bucket_that_never_solved_anything_is_unbounded_at_both_ends(
        self,
    ) -> None:
        cells = _repeated(MIN_CELLS_FOR_INTERVAL, tokens=1000, passing=())

        point = tokens_per_solved_by_achieved_depth(cells)[0]

        assert point.tokens_per_solved is None
        assert point.ci_low is None
        assert point.unbounded_above is True


class TestTheShapeOfAnInterval:
    """Four shapes, and a row cannot be two of them."""

    def _point(self, **fields: object) -> TokensPerSolvedPoint:
        base: dict[str, object] = {
            "depth": 1,
            "arm": Arm.GATED,
            "tokens": 10,
            "solved": 1,
            "cells": 3,
        }
        return TokensPerSolvedPoint.model_validate({**base, **fields})

    def test_an_upper_bound_needs_a_lower_one(self) -> None:
        with pytest.raises(ValueError, match="no lower bound"):
            self._point(ci_high=5.0)

    def test_an_open_top_has_to_say_so(self) -> None:
        with pytest.raises(ValueError, match="does not say it is unbounded"):
            self._point(ci_low=5.0)

    def test_a_bounded_top_cannot_also_be_open(self) -> None:
        with pytest.raises(ValueError, match="also claims to be unbounded"):
            self._point(ci_low=5.0, ci_high=9.0, unbounded_above=True)

    def test_a_reversed_interval_is_refused(self) -> None:
        with pytest.raises(ValueError, match="reversed"):
            self._point(ci_low=9.0, ci_high=5.0)


def _interval_point(
    arm: Arm, *, low: float | None, high: float | None, depth: int = 2
) -> TokensPerSolvedPoint:
    """One point with the given interval.

    Returns:
        The point.
    """
    return TokensPerSolvedPoint(
        depth=depth,
        arm=arm,
        tokens=1000,
        solved=1,
        cells=3,
        ci_low=low,
        ci_high=high,
        unbounded_above=low is not None and high is None,
    )


class TestWhenTheArmsCannotBeRanked:
    """Overlapping intervals are the finding, so they are named."""

    def test_overlapping_intervals_name_the_depth(self) -> None:
        points = (
            _interval_point(Arm.GATED, low=800.0, high=1200.0),
            _interval_point(Arm.UNGATED, low=1100.0, high=2000.0),
        )

        assert indistinguishable_depths(points) == (2,)

    def test_separated_intervals_do_not(self) -> None:
        points = (
            _interval_point(Arm.GATED, low=800.0, high=1000.0),
            _interval_point(Arm.UNGATED, low=1100.0, high=2000.0),
        )

        assert indistinguishable_depths(points) == ()

    def test_an_open_top_overlaps_everything_above_its_floor(self) -> None:
        points = (
            _interval_point(Arm.GATED, low=800.0, high=None),
            _interval_point(Arm.UNGATED, low=5000.0, high=6000.0),
        )

        assert indistinguishable_depths(points) == (2,)

    def test_a_depth_with_one_arm_says_nothing(self) -> None:
        assert (
            indistinguishable_depths(
                (_interval_point(Arm.GATED, low=800.0, high=1200.0),)
            )
            == ()
        )

    def test_an_absent_interval_says_nothing(self) -> None:
        points = (
            _interval_point(Arm.GATED, low=None, high=None),
            _interval_point(Arm.UNGATED, low=1100.0, high=2000.0),
        )

        assert indistinguishable_depths(points) == ()

    def test_depths_are_reported_ascending(self) -> None:
        points = (
            _interval_point(Arm.GATED, low=1.0, high=2.0, depth=3),
            _interval_point(Arm.UNGATED, low=1.0, high=2.0, depth=3),
            _interval_point(Arm.GATED, low=1.0, high=2.0, depth=1),
            _interval_point(Arm.UNGATED, low=1.0, high=2.0, depth=1),
        )

        assert indistinguishable_depths(points) == (1, 3)
