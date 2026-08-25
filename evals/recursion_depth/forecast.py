# module-kind: code
"""What one cell is expected to cost, from what this sweep has already measured.

The manifest's ``projected_branching`` is an assumption written before any tree
exists, and it is the one input a sweep can correct as it runs: every recorded
cell is a measurement of how wide this planner actually splits, against this
specification, with these models. Live cap-1 trees came back at 6 and 8 against
an assumed 4, so a forecast that keeps trusting the assumption is wrong in the
expensive direction by a factor that compounds with depth.

The estimate exists so the sweep can decline to START a cell it cannot finish.
That is a different job from the ceiling itself, which books sessions after
they run: a cell entered without the budget to complete it spends everything
left, records nothing measurable, and stops. Whatever it spent bought a cell
that enters no curve.

So the estimate errs HIGH throughout. Refusing a cell that would have fit ends
the sweep with budget unspent, losing one measurement; entering a cell that
does not fit loses that same measurement AND everything it spends on the way to
discovering it. The second is strictly worse, so ties go to refusing.
"""

from collections.abc import Sequence

from evals.recursion_depth.manifest import RecursionDepthManifest
from evals.recursion_depth.models import CellRecord


def measured_branching(cells: Sequence[CellRecord]) -> int | None:
    """The widest branching factor any recorded cell actually used.

    A tree of ``b`` branching and ``d`` achieved levels holds ``b ** d``
    leaves, so ``b`` is the ``d``-th root of the leaf count. Rounded UP, and
    taken as the maximum across cells rather than the mean, because both
    choices push the forecast high and the cost of forecasting low is a whole
    wasted cell.

    Cells that produced no tree are skipped: ``achieved_depth`` is ``None``
    precisely when there is nothing to measure branching from.

    Args:
        cells: Every cell recorded so far, measured or not.

    Returns:
        The widest factor observed, or ``None`` when nothing has been measured.
    """
    widest: int | None = None
    for cell in cells:
        depth = cell.achieved_depth
        leaves = len(cell.leaves)
        if depth is None or depth < 1 or leaves < 1:
            continue
        # Searched rather than computed as `leaves ** (1 / depth)`: floating
        # point puts a perfect square root of 36 at 5.999999999999999, and a
        # factor the tree demonstrably reached would be rounded below itself.
        #
        # Starting at two, which is what `projected_sessions` divides by and
        # what the manifest's own field requires. A single-leaf tree measures
        # a factor of one, which is not a shape the cost model describes.
        branching = 2
        while branching**depth < leaves:
            branching += 1
        widest = branching if widest is None else max(widest, branching)
    return widest


def estimate_sessions(
    manifest: RecursionDepthManifest,
    cells: Sequence[CellRecord],
    depth_cap: int,
) -> int:
    """What one cell at *depth_cap* is expected to cost, in sessions.

    Prefers a measurement of the same cap over any inference: the matrix
    repeats caps, so once one cap-3 cell has run, what a cap-3 cell costs is
    known rather than modelled. The maximum of those is taken for the same
    reason the branching factor is.

    Falling back, the manifest's own projection is reused with the measured
    branching substituted for the assumed one, so there is one cost model in
    the harness rather than a second one that can disagree with the figure the
    plan printed.

    Args:
        manifest: The matrix being recorded.
        cells: Every cell recorded so far, measured or not.
        depth_cap: The cap of the cell about to run.

    Returns:
        The expected session count.
    """
    same_cap = [
        cell.total_attempts
        for cell in cells
        if cell.depth_cap == depth_cap and cell.achieved_depth is not None
    ]
    if same_cap:
        return max(same_cap)
    return manifest.projected_sessions(depth_cap, branching=measured_branching(cells))
