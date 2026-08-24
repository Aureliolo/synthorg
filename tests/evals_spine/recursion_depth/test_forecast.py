# module-kind: tests
"""The sweep declines to start a cell its remaining budget cannot finish.

The ceiling books sessions AFTER they run, so on its own it can only stop a
sweep that has already overrun: a cell entered without the budget to complete
it spends everything left, records no ``achieved_depth`` and enters no curve.
The forecast is what makes that spend recoverable instead, so these pin the two
properties it needs. It must be built from what the sweep MEASURED, because the
manifest's assumed branching is the input a live run corrects; and it must err
HIGH, because forecasting low costs a whole cell rather than one measurement.
"""

from pathlib import Path

import pytest

from evals.recursion_depth.forecast import estimate_sessions, measured_branching
from evals.recursion_depth.manifest import Arm, load_manifest
from evals.recursion_depth.models import LEAF, CellRecord, UnitRecord
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_COMMITTED_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


def _cell(
    *,
    depth_cap: int,
    achieved_depth: int | None,
    leaves: int,
    attempts_each: int = 1,
) -> CellRecord:
    """Build a cell with *leaves* leaf units.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=depth_cap,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=achieved_depth,
        # The model requires an unavailable cell to say why, which is the same
        # invariant that makes `achieved_depth is None` a reliable "no tree".
        unavailable_reason=None if achieved_depth is not None else "no tree",
        units=tuple(
            UnitRecord(
                unit_id=NotBlankStr(f"leaf-{index}"),
                title=NotBlankStr("build it"),
                kind=LEAF,
                depth=1,
                delivered=True,
                attempts=attempts_each,
            )
            for index in range(leaves)
        ),
    )


class TestBranchingIsMeasuredNotAssumed:
    """The assumption is the thing a running sweep can correct about itself."""

    def test_nothing_measured_yet_reports_nothing(self) -> None:
        assert measured_branching(()) is None

    def test_a_cell_that_produced_no_tree_measures_nothing(self) -> None:
        # `achieved_depth` is None precisely when there is no tree to measure.
        unavailable = _cell(depth_cap=3, achieved_depth=None, leaves=0)

        assert measured_branching((unavailable,)) is None

    @pytest.mark.parametrize(
        ("depth", "leaves", "expected"),
        [
            (1, 6, 6),
            (1, 8, 8),
            (2, 36, 6),
            # Rounds UP: 30 leaves over two levels is not a whole factor, and
            # 5 would under-forecast a tree that reached wider than 5.
            (2, 30, 6),
        ],
        ids=["flat-six", "flat-eight", "square", "rounds-up"],
    )
    def test_the_factor_is_the_depth_th_root_of_the_leaf_count(
        self, depth: int, leaves: int, expected: int
    ) -> None:
        cell = _cell(depth_cap=depth, achieved_depth=depth, leaves=leaves)

        assert measured_branching((cell,)) == expected

    def test_the_widest_cell_wins(self) -> None:
        # Max rather than mean: both errors are not equal, and this one is the
        # cheap one.
        narrow = _cell(depth_cap=1, achieved_depth=1, leaves=4)
        wide = _cell(depth_cap=1, achieved_depth=1, leaves=9)

        assert measured_branching((narrow, wide)) == 9

    def test_a_single_leaf_never_reports_a_factor_of_one(self) -> None:
        # One would divide by zero in the manifest's own cost model.
        cell = _cell(depth_cap=1, achieved_depth=1, leaves=1)

        assert measured_branching((cell,)) == 2


class TestTheEstimatePrefersMeasurementOverInference:
    """A cap that has already run is known, not modelled."""

    def test_with_nothing_measured_it_falls_back_to_the_manifest(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)

        assert estimate_sessions(manifest, (), 2) == manifest.projected_sessions(2)

    def test_a_measured_cap_is_read_back_rather_than_projected(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)
        # Nine sessions against a projection built from an assumed branching of
        # four, so reading the measurement back is visibly not the projection.
        measured = _cell(depth_cap=1, achieved_depth=1, leaves=9, attempts_each=1)

        assert estimate_sessions(manifest, (measured,), 1) == 9

    def test_the_costliest_run_of_a_cap_is_the_estimate(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)
        cheap = _cell(depth_cap=1, achieved_depth=1, leaves=4)
        dear = _cell(depth_cap=1, achieved_depth=1, leaves=4, attempts_each=3)

        assert estimate_sessions(manifest, (cheap, dear), 1) == 12

    def test_an_unmeasured_cap_projects_from_measured_branching(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)
        wide = _cell(depth_cap=1, achieved_depth=1, leaves=8)

        estimate = estimate_sessions(manifest, (wide,), 3)

        # The whole point: a cap nothing has run is forecast from how wide this
        # planner actually splits, not from the manifest's guess.
        assert estimate == manifest.projected_sessions(3, branching=8)
        assert estimate > manifest.projected_sessions(3)
