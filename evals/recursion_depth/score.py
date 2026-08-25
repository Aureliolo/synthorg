# module-kind: code
"""The measurement: what fraction of the specification a merged tree satisfies.

After the root merge, the held-out oracle runs against the final tree and says
which of the specification's requirements it satisfies. That count over the
specification's own requirement count is the point this module plots, per
achieved depth and per cap, for each arm.

**This is not the question the sweep was built around, and the difference
matters.** That question is whether LEAF WORK survives the merge, and it wants a
denominator of work the leaves delivered. Measured on a live run, that
denominator does not hold up: a leaf must pass its own suite to count at all and
roughly a quarter did, a delivered leaf below the root often claims nothing, and
143 planner claims named no requirement the specification defines. Whole cells
came out with a zero denominator and therefore no point at all, including the
ungated arm at BOTH depths it was measured at, which deletes the arm comparison
that is the entire acceptance criterion.

So the denominator is the specification, which every cell shares, which cannot
empty, and which the same oracle grades. What that buys is a point for every
cell and two comparable arms at every depth. What it costs is attribution: a
tree scoring well because the merging agent rebuilt it and a tree scoring well
because the leaves' work survived are the same number here. The per-unit records
still carry the claim-level figures, so a later analysis can ask the narrower
question once the claim mapping is trustworthy.

**Depth is the depth the tree ACHIEVED.** The cap is what the run was allowed;
the planner decides what it uses. Binning on the cap makes caps the planner
never reached look like measured points, so the primary curve bins each run on
the depth it reached and the cap curve is reported beside it with the histogram
that says how much of the sweep was real.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import CellRecord, DepthPoint

#: Says which bucket a run belongs in, for both its fraction and its spend.
type RunBucket = Callable[[CellRecord], int]


def curve_by_achieved_depth(
    cells: Iterable[CellRecord], *, requirement_count: int
) -> tuple[DepthPoint, ...]:
    """Bin satisfaction on the depth each tree actually reached.

    Args:
        cells: The measured runs.
        requirement_count: The specification's own requirement count, from the
            report's provenance. Passed rather than read off a cell, because it
            is a property of the specification and duplicating it per cell
            creates a second owner a re-score could contradict.

    Returns:
        One point per ``(depth, arm)`` that any run reached, ascending.
    """
    return _curve(cells, bucket=_achieved_bucket, requirement_count=requirement_count)


def curve_by_depth_cap(
    cells: Iterable[CellRecord], *, requirement_count: int
) -> tuple[DepthPoint, ...]:
    """Bin satisfaction on the ``max_depth`` cap the run was allowed.

    The manipulated variable, kept beside the primary curve because a reader
    comparing the two can see how much of the sweep the planner used.

    Args:
        cells: The measured runs.
        requirement_count: The specification's own requirement count.

    Returns:
        One point per ``(cap, arm)``, ascending.
    """
    return _curve(cells, bucket=_cap_bucket, requirement_count=requirement_count)


def _achieved_bucket(cell: CellRecord) -> int:
    """Where a run belongs on the achieved-depth curve.

    ``achieved_depth`` already counts LEVELS, the same unit the cap is in
    (``tree.achieved_levels`` owns that conversion), so nothing is offset here.

    Returns:
        The number of levels the tree reached.
    """
    return cell.achieved_depth or 0


def _cap_bucket(cell: CellRecord) -> int:
    """Where a run belongs on the cap curve.

    Returns:
        The run's depth cap.
    """
    return cell.depth_cap


def _curve(
    cells: Iterable[CellRecord], *, bucket: RunBucket, requirement_count: int
) -> tuple[DepthPoint, ...]:
    """Fold every run into one point per ``(bin, arm)``.

    Summed across repetitions rather than averaged over them, which keeps one
    rule with the spend beside it. With a denominator identical for every cell
    the two agree anyway, so the choice is no longer load-bearing.

    Returns:
        The points, ordered by depth then arm.
    """
    required: dict[tuple[int, Arm], int] = defaultdict(int)
    satisfied: dict[tuple[int, Arm], int] = defaultdict(int)
    counted: dict[tuple[int, Arm], int] = defaultdict(int)
    cost: dict[tuple[int, Arm], float] = defaultdict(float)
    tokens: dict[tuple[int, Arm], int] = defaultdict(int)
    attempts: dict[tuple[int, Arm], int] = defaultdict(int)
    for cell in cells:
        if cell.achieved_depth is None:
            continue
        # One run, one bucket, for the fraction AND the spend. The two used to
        # be separate populations because a run contributed claims to every
        # level its leaves sat at while booking spend at one; scoring per cell
        # collapses that, so a single count is now the honest figure and two
        # would always be equal.
        slot = (bucket(cell), cell.arm)
        required[slot] += requirement_count
        satisfied[slot] += len(cell.merged_passing)
        counted[slot] += 1
        cost[slot] += cell.total_cost
        tokens[slot] += cell.total_tokens
        attempts[slot] += cell.total_attempts
    return tuple(
        DepthPoint(
            depth=depth,
            arm=arm,
            required=required[(depth, arm)],
            satisfied=satisfied[(depth, arm)],
            cells=counted[(depth, arm)],
            cost=cost[(depth, arm)],
            tokens=tokens[(depth, arm)],
            attempts=attempts[(depth, arm)],
        )
        for depth, arm in sorted(counted, key=lambda slot: (slot[0], slot[1].value))
    )


def achieved_depth_histogram(cells: Iterable[CellRecord]) -> dict[str, int]:
    """How many runs at each cap reached each depth, per arm.

    The reading the primary curve cannot be interpreted without: a flat right
    half means "gating holds at depth" only if runs went there.

    Split by arm, because each arm plans its own tree. Two arms compared at a
    depth only one of them reached is two different experiments on one axis,
    and pooling the counts would hide exactly that.

    Args:
        cells: The measured runs.

    Returns:
        ``"cap=<n> <arm> reached=<m>"`` mapped to the run count, in a stable
        order. Both numbers count LEVELS, so a cap spent in full reads
        ``cap=3 ... reached=3``.
    """
    counts: dict[str, int] = defaultdict(int)
    for cell in cells:
        if cell.achieved_depth is None:
            continue
        counts[
            f"cap={cell.depth_cap} {cell.arm.value} reached={cell.achieved_depth}"
        ] += 1
    return dict(sorted(counts.items()))


__all__ = [
    "achieved_depth_histogram",
    "curve_by_achieved_depth",
    "curve_by_depth_cap",
]
