# module-kind: tests
"""The survival metric, on hand-built trees.

The two decisions that decide the whole result are asserted here rather than
left to a real run to discover: what enters the denominator, and what the x-axis
means.
"""

import pytest

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import LEAF, MERGE, CellRecord, UnitRecord
from evals.recursion_depth.score import (
    achieved_depth_histogram,
    curve_by_achieved_depth,
    curve_by_depth_cap,
    spread_by_achieved_depth,
    spread_by_depth_cap,
    survival_by_achieved_depth,
    survival_by_depth_cap,
)
from evals.recursion_depth.tree import achieved_levels
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit


def _planning_task(label: str) -> Task:
    """Build a task a planning level can own.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(f"task:{label}"),
        title=NotBlankStr(label),
        description=NotBlankStr(f"Do {label}."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=NotBlankStr(sid("project:recursion-depth-score")),
        created_by=NotBlankStr("test"),
        status=TaskStatus.CREATED,
    )


def _level(parent: Task, *, depth: int, remaining: int) -> DecompositionResult:
    """Build the planning level at *depth* that split *parent*.

    Recurses while *remaining* levels are left, each splitting a task the level
    above it created, which is what the tree's own consistency rule requires.

    Returns:
        The level, carrying the rest of the chain below it.
    """
    label = f"level-{depth}"
    split = _planning_task(label)
    below = (
        (_level(split, depth=depth + 1, remaining=remaining - 1),)
        if remaining > 1
        else ()
    )
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=NotBlankStr(str(parent.id)),
            subtasks=(
                SubtaskDefinition(
                    id=NotBlankStr(str(split.id)),
                    title=NotBlankStr(label),
                    description=NotBlankStr(f"Build {label}."),
                    expected_artifacts=(NotBlankStr(f"{label}.py"),),
                ),
            ),
            task_structure=TaskStructure.SEQUENTIAL,
        ),
        created_tasks=(split,),
        depth=depth,
        children=below,
    )


def _chain(levels: int) -> DecompositionResult:
    """Build a tree of exactly *levels* planning levels, one branch wide.

    Returns:
        The root level.
    """
    return _level(_planning_task("root"), depth=0, remaining=levels)


class TestTheLevelCount:
    """``achieved_levels`` owns the index-to-count conversion, alone.

    Asserted directly rather than through a run, because the conversion is one
    ``+ 1`` and the whole depth axis rests on it: a run using its entire cap of
    three reported two, which reads as a tree that stopped a level short.
    """

    @pytest.mark.parametrize(("levels", "expected"), [(1, 1), (2, 2), (3, 3)])
    def test_a_cap_spent_in_full_reports_the_cap(
        self, levels: int, expected: int
    ) -> None:
        """A tree that never split is one level deep, never zero.

        ``max_depth=3`` admits levels 0, 1 and 2, so a tree that used all three
        reports three rather than the deepest index it happens to carry.
        """
        assert achieved_levels(_chain(levels)) == expected


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
        kind=LEAF,
        depth=depth,
        claimed=tuple(RequirementId(item) for item in claimed),
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

    Args:
        cap: How many levels the run was allowed.
        arm: Which arm produced it.
        achieved: How many levels it used, in the same unit as *cap*. A leaf's
            own ``depth`` is still a zero-based INDEX, so a tree whose deepest
            leaf sits at ``depth=1`` achieved two levels.
        units: The run's units.
        passing: What the merged tree satisfies.
        repetition: Index within the cell.

    Returns:
        The cell record.
    """
    return CellRecord(
        depth_cap=cap,
        arm=arm,
        repetition=repetition,
        achieved_depth=achieved,
        units=units,
        merged_passing=tuple(RequirementId(item) for item in passing),
    )


#: The specification's own requirement count, which every cell shares. Small
#: here so a fraction reads at a glance; the real spec carries 42.
_REQUIRED = 4


class TestWhatEntersTheDenominator:
    """The specification, for the curve that must produce a point per cell.

    Its denominator is identical for every cell and cannot empty, which is what
    makes the two arms comparable at every depth even where a cell's leaves all
    failed. What it costs is attribution, and that is what the survival curve
    beside it reports.
    """

    def test_the_denominator_is_the_specification(self) -> None:
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01", "R02"),
        )

        point = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)[0]

        assert point.required == _REQUIRED
        assert point.satisfied == 2
        assert point.fraction == pytest.approx(0.5)

    def test_a_requirement_listed_twice_is_satisfied_once(self) -> None:
        # `merged_passing` is a sequence, so it permits repeats, while the
        # denominator counts each requirement once. Counted naively a cell
        # listing R01 twice satisfies two of the one requirement, which either
        # inflates the fraction or trips the point's own subset check.
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",)),),
            passing=("R01", "R01"),
        )

        point = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)[0]

        assert point.satisfied == 1

    def test_a_cell_whose_leaves_all_failed_still_scores(self) -> None:
        # The case that produced NO POINT under the claim-based metric, in both
        # arms at both measured depths. A tree that satisfies nothing is a
        # measured zero, not an absence.
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",), delivered=False),),
            passing=(),
        )

        point = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)[0]

        assert point.required == _REQUIRED
        assert point.satisfied == 0
        assert point.fraction == pytest.approx(0.0)

    def test_what_a_leaf_claimed_does_not_reach_this_curve(self) -> None:
        # Deliberate: a tree scoring well because the merging agent rebuilt it
        # and one scoring well because leaf work survived are the same number
        # here. The survival curve is where that difference shows.
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02", "R03")),),
            passing=("R01",),
        )

        point = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)[0]

        # Three claimed and one passing, and neither three nor a third of it
        # appears: the point reads one satisfied against the whole spec.
        assert point.satisfied == 1
        assert point.required == _REQUIRED

    def test_two_cells_in_one_bucket_add_up(self) -> None:
        # Every other case here puts one cell in a bucket, so nothing would
        # notice a fold that overwrote instead of accumulating.
        first = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",)),),
            passing=("R01",),
        )
        second = _cell(
            cap=2,
            achieved=2,
            repetition=1,
            units=(_leaf("b", depth=1, claimed=("R02",)),),
            passing=("R02", "R03"),
        )

        point = curve_by_achieved_depth((first, second), requirement_count=_REQUIRED)[0]

        assert point.cells == 2
        assert point.required == 2 * _REQUIRED
        assert point.satisfied == 3

    def test_a_merge_unit_does_not_change_the_score(self) -> None:
        merge = UnitRecord(
            unit_id=NotBlankStr("root"),
            title=NotBlankStr("assemble"),
            kind=MERGE,
            depth=0,
            claimed=(RequirementId("R09"),),
            delivered=True,
        )
        cell = _cell(
            cap=2,
            achieved=2,
            units=(merge, _leaf("a", depth=1, claimed=("R01",))),
            passing=("R01",),
        )

        point = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)[0]

        assert point.satisfied == 1


class TestLeafWorkSurvival:
    """The question the sweep was built around, beside the adjacent one.

    Of what the delivered leaves claimed, how much did the merge keep. The
    denominator is leaf work rather than the specification, so it CAN empty,
    and the empty case is reported as absent rather than as a zero: nothing was
    measured there, and a zero says everything was lost.
    """

    def test_only_delivered_leaves_enter_the_denominator(self) -> None:
        """Work that never worked cannot be work the merge lost."""
        cell = _cell(
            cap=2,
            achieved=2,
            units=(
                _leaf("a", depth=1, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R02",), delivered=False),
            ),
            passing=("R01", "R02"),
        )

        point = survival_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 1
        assert point.surviving_claims == 1
        assert point.fraction == pytest.approx(1.0)

    def test_a_claim_the_merge_dropped_lowers_the_fraction(self) -> None:
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01",),
        )

        point = survival_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 2
        assert point.surviving_claims == 1
        assert point.fraction == pytest.approx(0.5)

    def test_a_bucket_with_no_attributable_work_has_no_point_value(self) -> None:
        """Absent, not zero: the two read as opposite conclusions."""
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",), delivered=False),),
            passing=("R01",),
        )

        point = survival_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 0
        assert point.fraction is None

    def test_two_leaves_claiming_one_requirement_count_it_once(self) -> None:
        # Overlapping units are a property of the plan, not more work, and
        # counting them twice weights the level by how repetitive it was.
        cell = _cell(
            cap=2,
            achieved=2,
            units=(
                _leaf("a", depth=1, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R01",)),
            ),
            passing=("R01",),
        )

        point = survival_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 1

    def test_a_merge_unit_contributes_nothing(self) -> None:
        merge = UnitRecord(
            unit_id=NotBlankStr("root"),
            title=NotBlankStr("assemble"),
            kind=MERGE,
            depth=0,
            claimed=(RequirementId("R09"),),
            delivered=True,
        )
        cell = _cell(
            cap=2,
            achieved=2,
            units=(merge, _leaf("a", depth=1, claimed=("R01",))),
            passing=("R01",),
        )

        point = survival_by_achieved_depth((cell,))[0]

        assert point.delivered_claims == 1

    def test_both_curves_bin_a_cell_on_the_same_axis(self) -> None:
        """One chart, two lines: a shared x is what makes them comparable."""
        cell = _cell(
            cap=4,
            achieved=3,
            units=(_leaf("a", depth=2, claimed=("R01",)),),
            passing=("R01",),
        )

        spec = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)
        survival = survival_by_achieved_depth((cell,))

        assert [point.depth for point in spec] == [point.depth for point in survival]

    def test_the_cap_curve_bins_on_the_cap(self) -> None:
        cell = _cell(
            cap=4,
            achieved=3,
            units=(_leaf("a", depth=2, claimed=("R01",)),),
            passing=("R01",),
        )

        assert survival_by_depth_cap((cell,))[0].depth == 4

    def test_the_two_curves_come_apart_where_the_merge_rebuilt_the_work(
        self,
    ) -> None:
        """The whole reason both are reported.

        A merged tree satisfying most of the specification while almost none of
        its leaves' own work survived is exactly the reading the specification
        curve cannot distinguish from a tree whose leaves carried it.
        """
        cell = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02", "R03", "R04")),),
            passing=("R01", "R05", "R06", "R07"),
        )

        spec = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)[0]
        survival = survival_by_achieved_depth((cell,))[0]

        assert spec.fraction == pytest.approx(1.0)
        assert survival.fraction == pytest.approx(0.25)

    def test_two_cells_in_one_bucket_add_up(self) -> None:
        first = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",)),),
            passing=("R01",),
        )
        second = _cell(
            cap=2,
            achieved=2,
            repetition=1,
            units=(_leaf("b", depth=1, claimed=("R02", "R03")),),
            passing=("R02",),
        )

        point = survival_by_achieved_depth((first, second))[0]

        assert point.cells == 2
        assert point.delivered_claims == 3
        assert point.surviving_claims == 2

    def test_each_arm_gets_its_own_point(self) -> None:
        gated = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",)),),
            passing=("R01",),
        )
        ungated = _cell(
            cap=2,
            arm=Arm.UNGATED,
            achieved=2,
            units=(_leaf("b", depth=1, claimed=("R02",)),),
            passing=(),
        )

        points = survival_by_achieved_depth((gated, ungated))

        assert [point.arm for point in points] == [Arm.GATED, Arm.UNGATED]
        assert points[0].fraction == pytest.approx(1.0)
        assert points[1].fraction == pytest.approx(0.0)


class TestWhatTheAxisMeans:
    """The depth a tree reached, not the cap it was allowed."""

    def test_a_run_lands_at_the_depth_it_reached(self) -> None:
        cell = _cell(
            cap=4,
            achieved=3,
            units=(
                _leaf("shallow", depth=0, claimed=("R01",)),
                _leaf("deep", depth=2, claimed=("R02", "R03")),
            ),
            passing=("R01", "R02"),
        )

        points = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)

        # One point, at the depth the TREE reached. Binning each leaf on its own
        # level made one run several points, so a run's spend and its score were
        # over different populations.
        assert [point.depth for point in points] == [3]
        assert points[0].satisfied == 2
        assert points[0].cells == 1

    def test_the_cap_curve_pools_a_run_at_its_cap(self) -> None:
        cell = _cell(
            cap=4,
            achieved=3,
            units=(
                _leaf("shallow", depth=0, claimed=("R01",)),
                _leaf("deep", depth=2, claimed=("R02",)),
            ),
            passing=("R01",),
        )

        points = curve_by_depth_cap((cell,), requirement_count=_REQUIRED)

        assert len(points) == 1
        assert points[0].depth == 4
        assert points[0].required == _REQUIRED

    def test_the_histogram_says_how_far_each_cap_actually_went(self) -> None:
        # Without it, three caps that produced identical trees look like three
        # measured points.
        cells = (
            _cell(
                cap=4,
                achieved=3,
                units=(_leaf("a", depth=2, claimed=("R01",)),),
                passing=(),
            ),
            _cell(
                cap=5,
                achieved=3,
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
                achieved=4,
                units=(_leaf("a", depth=3, claimed=("R01",)),),
                passing=(),
            ),
            _cell(
                cap=4,
                arm=Arm.UNGATED,
                achieved=2,
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
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01", "R02"),
        )
        ungated = _cell(
            cap=2,
            arm=Arm.UNGATED,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02")),),
            passing=("R01",),
        )

        curve = curve_by_achieved_depth((gated, ungated), requirement_count=2)
        points = {point.arm: point for point in curve}

        assert points[Arm.GATED].fraction == pytest.approx(1.0)
        assert points[Arm.UNGATED].fraction == pytest.approx(0.5)

    def test_a_run_books_its_cost_once(self) -> None:
        cell = _cell(
            cap=3,
            achieved=3,
            units=(
                _leaf("a", depth=0, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R02",)),
                _leaf("c", depth=2, claimed=("R03",)),
            ),
            passing=(),
        )

        total = sum(
            point.cost
            for point in curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)
        )

        assert total == pytest.approx(cell.total_cost)

    def test_a_run_is_one_population_for_score_and_for_spend(self) -> None:
        # One count, because a run contributes to exactly one bucket for both
        # its fraction and its spend. A second population column would always
        # equal the first, and two equal numbers invite a reader to hunt for a
        # difference that is not there.
        cell = _cell(
            cap=3,
            achieved=3,
            units=(
                _leaf("a", depth=0, claimed=("R01",)),
                _leaf("b", depth=1, claimed=("R02",)),
                _leaf("c", depth=2, claimed=("R03",)),
            ),
            passing=("R01", "R02", "R03"),
        )

        points = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)

        assert [point.depth for point in points] == [3]
        assert points[0].cells == 1
        assert points[0].cost == pytest.approx(cell.total_cost)

    def test_a_run_whose_leaves_all_failed_still_books_its_spend(self) -> None:
        # An ordinary point scoring zero, which is what keeps it in the cost
        # panel: a panel filtered on a claims population would drop exactly
        # these runs, and at the deep end they are the most expensive in the
        # sweep.
        cell = _cell(
            cap=5,
            achieved=5,
            units=(
                _leaf("a", depth=3, claimed=("R01",), delivered=False),
                _leaf("b", depth=4, claimed=("R02",), delivered=False),
            ),
            passing=(),
        )

        points = curve_by_achieved_depth((cell,), requirement_count=_REQUIRED)

        assert [point.depth for point in points] == [5]
        assert points[0].cells == 1
        assert points[0].cost == pytest.approx(cell.total_cost)
        assert points[0].fraction == pytest.approx(0.0)

    def test_an_unavailable_cell_contributes_nothing(self) -> None:
        unavailable = CellRecord(
            depth_cap=3,
            arm=Arm.GATED,
            repetition=0,
            unavailable_reason="the provider was gone",
        )

        assert (
            curve_by_achieved_depth((unavailable,), requirement_count=_REQUIRED) == ()
        )
        assert achieved_depth_histogram((unavailable,)) == {}


class TestPerDepthSpread:
    """Three repetitions exist to show variance, so the variance is reported.

    The curves POOL a bucket's repetitions into one fraction, which is the
    right shape for a rate over work and says nothing about whether a low point
    is one bad draw or three consistent ones. That question is the whole reason
    a cap is recorded more than once, so it gets its own view rather than
    being left for a reader to reconstruct from `cells`.
    """

    def _three(self) -> tuple[CellRecord, ...]:
        """Three runs of one cap, scoring 1, 2 and 4 of four requirements.

        Returns:
            The cells.
        """
        return tuple(
            _cell(
                cap=3,
                achieved=3,
                repetition=index,
                units=(_leaf(f"a{index}", depth=2, claimed=("R01", "R02")),),
                passing=passing,
            )
            for index, passing in enumerate(
                [("R01",), ("R01", "R02"), ("R01", "R02", "R03", "R04")]
            )
        )

    def test_a_bucket_reports_its_range_rather_than_its_sum(self) -> None:
        spread = spread_by_achieved_depth(self._three(), requirement_count=_REQUIRED)[0]

        assert spread.depth == 3
        assert spread.cells == 3
        assert (spread.satisfied_min, spread.satisfied_median) == (1, 2)
        assert spread.satisfied_max == 4

    def test_survival_is_ranged_per_run_rather_than_pooled(self) -> None:
        spread = spread_by_achieved_depth(self._three(), requirement_count=_REQUIRED)[0]

        # Each run claimed R01 and R02 through one delivered leaf, so the three
        # survival rates are 1 of 2, 2 of 2 and 2 of 2.
        assert spread.survival_min == pytest.approx(0.5)
        assert spread.survival_median == pytest.approx(1.0)
        assert spread.survival_max == pytest.approx(1.0)

    def test_a_run_with_no_attributable_work_is_absent_from_the_range(self) -> None:
        # The absent-point rule, applied per RUN rather than per bucket: a run
        # whose delivered leaves claimed nothing has no rate, and folding it in
        # as a zero would report a collapse that was never measured.
        nothing_claimed = _cell(
            cap=3,
            achieved=3,
            repetition=3,
            units=(_leaf("b", depth=2, claimed=("R01",), delivered=False),),
            passing=(),
        )

        spread = spread_by_achieved_depth(
            (*self._three(), nothing_claimed), requirement_count=_REQUIRED
        )[0]

        assert spread.cells == 4
        assert spread.survival_min == pytest.approx(0.5)
        assert spread.satisfied_min == 0

    def test_a_bucket_where_nothing_was_attributable_has_no_range(self) -> None:
        nothing_claimed = _cell(
            cap=3,
            achieved=3,
            units=(_leaf("b", depth=2, claimed=("R01",), delivered=False),),
            passing=(),
        )

        spread = spread_by_achieved_depth(
            (nothing_claimed,), requirement_count=_REQUIRED
        )[0]

        assert spread.survival_min is None
        assert spread.survival_median is None
        assert spread.survival_max is None

    def test_it_bins_on_the_same_seam_as_the_curves(self) -> None:
        cells = self._three()

        point = curve_by_achieved_depth(cells, requirement_count=_REQUIRED)[0]
        spread = spread_by_achieved_depth(cells, requirement_count=_REQUIRED)[0]

        assert (spread.depth, spread.arm, spread.cells) == (
            point.depth,
            point.arm,
            point.cells,
        )

    def test_the_cap_view_bins_on_the_cap(self) -> None:
        shallow = _cell(
            cap=4,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01",)),),
            passing=("R01",),
        )

        by_cap = spread_by_depth_cap((shallow,), requirement_count=_REQUIRED)[0]
        by_achieved = spread_by_achieved_depth((shallow,), requirement_count=_REQUIRED)[
            0
        ]

        assert by_cap.depth == 4
        assert by_achieved.depth == 2

    def test_an_unavailable_cell_contributes_no_range(self) -> None:
        unavailable = CellRecord(
            depth_cap=3,
            arm=Arm.GATED,
            repetition=0,
            unavailable_reason="the provider was gone",
        )

        spread = spread_by_achieved_depth((unavailable,), requirement_count=_REQUIRED)

        assert spread == ()
