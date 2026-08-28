# module-kind: code
"""What one cell is expected to cost, so the sweep can decline to start it.

The estimate exists so a sweep can refuse a cell it cannot finish. That is a
different job from the ceiling itself, which books sessions after they run: a
cell entered without the budget to complete it spends everything left, records
nothing measurable, and stops. Whatever it spent bought a cell that enters no
curve.

Two sources, in that order. A cap the matrix has already recorded is KNOWN, so
the costliest of those runs is the estimate: the matrix repeats caps, and once
one cap-3 cell has run there is nothing left to model. A cap nothing has run
takes the manifest's declared ``expected_sessions_per_cell``.

Declared, rather than derived from ``projected_sessions``, and the difference is
what this module is for. That model assumes uniform branching, and these trees
do not branch uniformly: a recorded cap-3 tree split 7 ways at the root, 4.6
ways at level 1 and 3.5 ways at level 2, so the widest factor any cell shows is
the ROOT's, and raising it to the fourth power answered 3,601 sessions for a
cap-4 cell whose real cost is near 300. Used to decide whether a cell starts,
that refuses the deepest cell of every sweep: the check written to save a cell's
spend would instead have cost the measurement the sweep exists to take.

The estimate still errs HIGH, because the asymmetry that made it err high has
not changed. Refusing a cell that would have fit ends the sweep with budget
unspent, losing one measurement; entering a cell that does not fit loses that
same measurement AND everything it spends on the way to discovering it. What
changed is that the margin is now a figure an operator sized from measurement
rather than an artefact of the wrong tree shape.
"""

from collections.abc import Sequence

from evals.recursion_depth.manifest import RecursionDepthManifest
from evals.recursion_depth.models import CellRecord


def estimate_sessions(
    manifest: RecursionDepthManifest,
    cells: Sequence[CellRecord],
    depth_cap: int,
) -> int:
    """What one cell at *depth_cap* is expected to cost, in sessions.

    Args:
        manifest: The matrix being recorded.
        cells: Every cell recorded so far, measured or not. A cell that
            produced no tree is not a measurement of anything: it stopped
            before the cost it would have had.
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
    return manifest.expected_sessions(depth_cap)
