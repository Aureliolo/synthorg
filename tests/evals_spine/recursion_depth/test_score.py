# module-kind: tests
"""The survival metric, on hand-built trees.

The two decisions that decide the whole result are asserted here rather than
left to a real run to discover: what enters the denominator, and what the x-axis
means.
"""

import pytest

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import LEAF, MERGE, CellRecord, UnitRecord
from evals.recursion_depth.score import (
    achieved_depth_histogram,
    curve_by_achieved_depth,
    curve_by_depth_cap,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _leaf(
    unit_id: str, *, depth: int, claimed: tuple[str, ...], delivered: bool = True
) -> UnitRecord:
    """Build one leaf record.

    Returns:
        The unit record.
    """
    return UnitRecord(
        unit_id=NotBlankStr(unit_id),
        title=NotBlankStr(f"unit {unit_id}"),
        kind=NotBlankStr(LEAF),
        depth=depth,
        claimed=tuple(NotBlankStr(item) for item in claimed),
        delivered=delivered,
        attempts=1,
        cost=1.0,
    )


def _cell(
    *,
    cap: int,
    arm: Arm = Arm.GATED,
    achieved: int,
    units: tuple[UnitRecord, ...],
    passing: tuple[str, ...],
    repetition: int = 0,
) -> CellRecord:
    """Build one measured run.

    Returns:
        The cell record.
    """
    return CellRecord(
        depth_cap=cap,
        arm=arm,
        repetition=repetition,
        achieved_depth=achieved,
        units=units,
        merged_passing=tuple(NotBlankStr(item) for item in passing),
    )


class TestWhatEntersTheDenominator:
    """Leaf work DELIVERED, not leaf work that stood up on its own."""

    def test_a_delivered_leaf_claim_that_survives_counts_once(self) -> None:
        cell = _cell(
            cap=2,
            achieved=1,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01",),
        )

        point = curve_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 2
        assert point.surviving_claims == 1
        assert point.fraction == pytest.approx(0.5)

    def test_a_leaf_that_never_delivered_is_not_work_the_merge_lost(self) -> None:
        # Its claims are absent from both halves of the ratio. Counting them in
        # the denominator would report a merge failure for work nobody built.
        cell = _cell(
            cap=2,
            achieved=1,
            units=(
                _leaf("a", depth=1, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R02",), delivered=False),
            ),
            passing=("R01",),
        )

        point = curve_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 1
        assert point.fraction == pytest.approx(1.0)

    def test_two_leaves_claiming_the_same_requirement_count_once(self) -> None:
        # A planner producing overlapping units would otherwise weight its
        # level by how repetitive the plan was.
        cell = _cell(
            cap=2,
            achieved=1,
            units=(
                _leaf("a", depth=1, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R01",)),
            ),
            passing=(),
        )

        point = curve_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 1

    def test_a_depth_where_nothing_delivered_reports_no_rate(self) -> None:
        # An absence, not a zero: a zero says the merge lost everything.
        cell = _cell(
            cap=2,
            achieved=1,
            units=(_leaf("a", depth=1, claimed=("R01",), delivered=False),),
            passing=(),
        )

        points = curve_by_achieved_depth((cell,))

        assert all(point.fraction is None for point in points)

    def test_a_merge_unit_contributes_no_claims(self) -> None:
        merge = UnitRecord(
            unit_id=NotBlankStr("root"),
            title=NotBlankStr("assemble"),
            kind=NotBlankStr(MERGE),
            depth=0,
            claimed=(NotBlankStr("R09"),),
            delivered=True,
        )
        cell = _cell(
            cap=2,
            achieved=1,
            units=(merge, _leaf("a", depth=1, claimed=("R01",))),
            passing=("R01", "R09"),
        )

        point = curve_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 1


class TestWhatTheAxisMeans:
    """The depth a tree reached, not the cap it was allowed."""

    def test_leaves_are_binned_on_their_own_level(self) -> None:
        cell = _cell(
            cap=4,
            achieved=2,
            units=(
                _leaf("shallow", depth=0, claimed=("R01",)),
                _leaf("deep", depth=2, claimed=("R02", "R03")),
            ),
            passing=("R01", "R02"),
        )

        points = {point.depth: point for point in curve_by_achieved_depth((cell,))}

        assert points[1].delivered_claims == 1
        assert points[3].delivered_claims == 2
        assert points[3].surviving_claims == 1

    def test_the_cap_curve_pools_a_run_at_its_cap(self) -> None:
        cell = _cell(
            cap=4,
            achieved=2,
            units=(
                _leaf("shallow", depth=0, claimed=("R01",)),
                _leaf("deep", depth=2, claimed=("R02",)),
            ),
            passing=("R01",),
        )

        points = curve_by_depth_cap((cell,))

        assert len(points) == 1
        assert points[0].depth == 4
        assert points[0].delivered_claims == 2

    def test_the_histogram_says_how_far_each_cap_actually_went(self) -> None:
        # Without it, three caps that produced identical trees look like three
        # measured points.
        cells = (
            _cell(
                cap=4,
                achieved=2,
                units=(_leaf("a", depth=2, claimed=("R01",)),),
                passing=(),
            ),
            _cell(
                cap=5,
                achieved=2,
                repetition=1,
                units=(_leaf("b", depth=2, claimed=("R02",)),),
                passing=(),
            ),
        )

        assert achieved_depth_histogram(cells) == {
            "cap=4 gated reached=3": 1,
            "cap=5 gated reached=3": 1,
        }

    def test_the_histogram_keeps_the_arms_apart(self) -> None:
        # Each arm plans its own tree, so two arms compared at a depth only one
        # of them reached is two experiments on one axis. Pooled counts hide it.
        cells = (
            _cell(
                cap=4,
                arm=Arm.GATED,
                achieved=3,
                units=(_leaf("a", depth=3, claimed=("R01",)),),
                passing=(),
            ),
            _cell(
                cap=4,
                arm=Arm.UNGATED,
                achieved=1,
                units=(_leaf("b", depth=1, claimed=("R02",)),),
                passing=(),
            ),
        )

        assert achieved_depth_histogram(cells) == {
            "cap=4 gated reached=4": 1,
            "cap=4 ungated reached=2": 1,
        }


class TestArmsAndCost:
    """The two lines, and what each of them spent."""

    def test_the_arms_are_separate_lines(self) -> None:
        gated = _cell(
            cap=2,
            arm=Arm.GATED,
            achieved=1,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01", "R02"),
        )
        ungated = _cell(
            cap=2,
            arm=Arm.UNGATED,
            achieved=1,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01",),
        )

        curve = curve_by_achieved_depth((gated, ungated))
        points = {point.arm: point for point in curve}

        assert points[Arm.GATED].fraction == pytest.approx(1.0)
        assert points[Arm.UNGATED].fraction == pytest.approx(0.5)

    def test_a_run_books_its_cost_once(self) -> None:
        # Booking it in every bucket a leaf landed in would multiply the
        # sweep's spend by the tree's height.
        cell = _cell(
            cap=3,
            achieved=2,
            units=(
                _leaf("a", depth=0, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R02",)),
                _leaf("c", depth=2, claimed=("R03",)),
            ),
            passing=(),
        )

        total = sum(point.cost for point in curve_by_achieved_depth((cell,)))

        assert total == pytest.approx(cell.total_cost)

    def test_an_unavailable_cell_contributes_nothing(self) -> None:
        unavailable = CellRecord(
            depth_cap=3,
            arm=Arm.GATED,
            repetition=0,
            unavailable_reason="the provider was gone",
        )

        assert curve_by_achieved_depth((unavailable,)) == ()
        assert achieved_depth_histogram((unavailable,)) == {}
