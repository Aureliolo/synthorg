# module-kind: tests
"""The sweep declines to start a cell its remaining budget cannot finish.

The ceiling books sessions AFTER they run, so on its own it can only stop a
sweep that has already overrun: a cell entered without the budget to complete
it spends everything left, records no ``achieved_depth`` and enters no curve.
The forecast is what makes that spend recoverable instead.

Two properties, and the second is the one a live matrix turns on. It must
prefer a MEASUREMENT of the same cap over anything modelled, because the matrix
repeats caps. And for a cap nothing has run it must answer a figure somebody
sized from measurement, NOT a full-tree projection: a projection is the
scenario that sizes the ceiling, and using it to decide whether a cell starts
refuses the deepest cells of every sweep.
"""

from pathlib import Path

import pytest

from evals.recursion_depth.forecast import estimate_sessions
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


class TestTheEstimatePrefersMeasurementOverInference:
    """A cap that has already run is known, not modelled."""

    def test_with_nothing_measured_it_takes_the_declared_cost(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)

        assert estimate_sessions(manifest, (), 2) == manifest.expected_sessions(2)

    def test_a_measured_cap_is_read_back_rather_than_declared(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)
        measured = _cell(depth_cap=1, achieved_depth=1, leaves=9, attempts_each=1)

        assert estimate_sessions(manifest, (measured,), 1) == 9

    def test_the_costliest_run_of_a_cap_is_the_estimate(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)
        cheap = _cell(depth_cap=1, achieved_depth=1, leaves=4)
        dear = _cell(depth_cap=1, achieved_depth=1, leaves=4, attempts_each=3)

        assert estimate_sessions(manifest, (cheap, dear), 1) == 12

    def test_a_cell_that_produced_no_tree_is_not_a_measurement(self) -> None:
        manifest = load_manifest(_COMMITTED_MANIFEST)
        unavailable = _cell(depth_cap=2, achieved_depth=None, leaves=0)

        assert estimate_sessions(
            manifest, (unavailable,), 2
        ) == manifest.expected_sessions(2)


class TestTheDeepestCapIsStillReachable:
    """The regression this module exists for, in the shape that produced it."""

    def test_a_shallow_wide_cell_does_not_price_the_deep_end_out(self) -> None:
        """Trees branch WIDE at the top and narrow below, so ``b ** d`` lies.

        The first sweep's cap-1 cell held 7 leaves over one level, its cap-2
        cell 38 over two, its cap-3 cell 58 over three: per-level fan-out of 7,
        then 4.6, then 3.5. A model taking the widest factor any cell showed
        and raising it to the fourth power answered 3,601 sessions for a cap-4
        cell whose real cost is near 300, so the check that exists to SAVE a
        cell's spend would have refused every cap-4 cell of every sweep, and
        the run would stop having re-measured what it already knew.
        """
        manifest = load_manifest(_COMMITTED_MANIFEST)
        recorded = (
            _cell(depth_cap=1, achieved_depth=1, leaves=7),
            _cell(depth_cap=2, achieved_depth=2, leaves=38),
            _cell(depth_cap=3, achieved_depth=3, leaves=58),
        )

        estimate = estimate_sessions(manifest, recorded, 4)

        assert estimate == manifest.expected_sessions(4)
        # The figure the old model would have produced from the same cells.
        assert estimate < manifest.projected_sessions(4, branching=7)

    def test_the_declared_costs_leave_the_matrix_affordable(self) -> None:
        """Every planned cell can be STARTED under the shipped ceiling.

        Checked against the manifest itself rather than against a copy of its
        numbers: an operator lowering one and raising another is exactly the
        edit that leaves the deepest cells unreachable, and it would otherwise
        surface hours into a paid run.
        """
        manifest = load_manifest(_COMMITTED_MANIFEST)

        # The worst case the refusal check ever sees: every earlier cell cost
        # its declared figure, and the deepest cell is still asked for.
        spent = sum(
            manifest.repetitions[depth] * manifest.expected_sessions(depth)
            for depth in manifest.depths
            if depth < max(manifest.depths)
        )
        deepest = manifest.expected_sessions(max(manifest.depths))

        assert spent + deepest <= manifest.max_sessions
