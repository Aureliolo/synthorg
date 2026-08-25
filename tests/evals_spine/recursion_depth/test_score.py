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
    three reported two, which reads as a tree that stopped a level short, and
    that is the reading an external reviewer took.
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
    """The specification, not the work the leaves happened to claim.

    The sweep was built to ask whether leaf work survives the merge, and that
    denominator did not hold up on a live run: a leaf must pass its own suite to
    count, roughly a quarter did, and whole cells came out with nothing in the
    denominator and therefore no point at all. Both arms lost their depth-2 and
    depth-3 points that way, which deletes the comparison the sweep exists for.
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

    def test_what_a_leaf_claimed_does_not_reach_the_curve(self) -> None:
        # Deliberate, and the cost of the change: a tree scoring well because
        # the merging agent rebuilt it and one scoring well because leaf work
        # survived are the same number here. The per-unit records still carry
        # the claims, so the narrower question stays askable later.
        claiming = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=("R01", "R02", "R03")),),
            passing=("R01",),
        )
        silent = _cell(
            cap=2,
            achieved=2,
            units=(_leaf("a", depth=1, claimed=()),),
            passing=("R01",),
        )

        assert (
            curve_by_achieved_depth((claiming,), requirement_count=_REQUIRED)[
                0
            ].fraction
            == curve_by_achieved_depth((silent,), requirement_count=_REQUIRED)[
                0
            ].fraction
        )

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
        # These used to be two counts, because a run contributed claims at every
        # level its leaves sat at while booking spend at one. Scoring per cell
        # collapses that, so a second count would now always equal the first and
        # two equal numbers invite a reader to look for the difference.
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
        # It used to exist only in the cost population, so filtering the cost
        # panel on the claims population dropped exactly these runs, which at
        # the deep end are the most expensive in the sweep. Now it is an
        # ordinary point scoring zero, and the panel keeps it either way.
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
