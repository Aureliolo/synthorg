# module-kind: code
"""The measurement: what fraction of leaf work survives to a correct merge.

For each leaf that DELIVERED (it produced its declared artifacts and its own
tests passed in its own tree), the spec requirements it claimed are leaf work
delivered. After the root merge, the held-out oracle runs against the final
tree. The fraction of those claims still satisfied is the survival rate, which
is the ARIES deterioration ratio measured with the gate on and with it off.

Two decisions in here carry the whole result.

**Delivery, not standalone correctness, is the denominator.** A leaf's own tree
usually cannot run the spec oracle at all: at depth 5 a unit is one function and
nothing above it exists yet. Requiring a standalone pass would empty the
denominator exactly where the curve is most interesting, so the denominator is
work the leaf DELIVERED and the numerator is the part of it the merge kept. A
stricter standalone variant is recorded beside it and kept off the chart.

**Depth is the depth the tree ACHIEVED.** The cap is what the run was allowed;
the planner decides what it uses. Binning on the cap makes caps the planner
never reached look like measured points, so the primary curve bins each leaf on
its own level and the cap curve is reported beside it with the histogram that
says how much of the sweep was real.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import CellRecord, DepthPoint

#: Splits one run's claims into ``(delivered, surviving)`` per bucket.
type Splitter = Callable[[CellRecord], dict[int, tuple[set[str], set[str]]]]

#: Says which bucket a run's own spend and session count belong in.
type RunBucket = Callable[[CellRecord], int]

#: A leaf's own ``depth`` is a level INDEX, zero at the root, and the report
#: asks its question in levels: a run that never split is one level deep rather
#: than zero. Applies to a unit's depth only. A cell's ``achieved_depth`` and
#: its ``depth_cap`` are both level COUNTS already (see
#: ``tree.achieved_levels``), so offsetting either would count one level twice.
_DEPTH_OFFSET = 1


def _claims_by_depth(cell: CellRecord) -> dict[int, set[str]]:
    """Group the requirements delivered leaves claimed, by the leaf's depth.

    Deduplicated within a level: two leaves claiming the same requirement is a
    planner producing overlapping units, and counting it twice would weight
    that level by how repetitive the plan was.

    Returns:
        The claimed requirement ids per reported depth.
    """
    claims: dict[int, set[str]] = defaultdict(set)
    for leaf in cell.leaves:
        if not leaf.delivered:
            continue
        claims[leaf.depth + _DEPTH_OFFSET].update(leaf.claimed)
    return dict(claims)


def curve_by_achieved_depth(cells: Iterable[CellRecord]) -> tuple[DepthPoint, ...]:
    """Bin survival on the depth each leaf actually sat at.

    Args:
        cells: The measured runs.

    Returns:
        One point per ``(depth, arm)`` that any leaf reached, ascending.
    """
    return _curve(cells, key=_by_leaf_depth, run_bucket=_achieved_bucket)


def curve_by_depth_cap(cells: Iterable[CellRecord]) -> tuple[DepthPoint, ...]:
    """Bin survival on the ``max_depth`` cap the run was allowed.

    The manipulated variable, kept beside the primary curve because a reader
    comparing the two can see how much of the sweep the planner used.

    Args:
        cells: The measured runs.

    Returns:
        One point per ``(cap, arm)``, ascending.
    """
    return _curve(cells, key=_by_cap, run_bucket=_cap_bucket)


def _achieved_bucket(cell: CellRecord) -> int:
    """Where a run's own cost belongs on the achieved-depth curve.

    No offset, unlike the per-leaf splitter above: ``achieved_depth`` already
    counts LEVELS, the same unit the cap is in (``tree.achieved_levels`` owns
    that conversion), while a leaf's own ``depth`` is a zero-based index.
    Offsetting here would count one level twice.

    Returns:
        The number of levels the tree reached.
    """
    return cell.achieved_depth or 0


def _cap_bucket(cell: CellRecord) -> int:
    """Where a run's own cost belongs on the cap curve.

    Returns:
        The run's depth cap.
    """
    return cell.depth_cap


def _by_leaf_depth(cell: CellRecord) -> dict[int, tuple[set[str], set[str]]]:
    """Split one run's claims into ``(delivered, surviving)`` per leaf depth.

    Returns:
        The two sets per reported depth.
    """
    passing = set(cell.merged_passing)
    return {
        depth: (claims, claims & passing)
        for depth, claims in _claims_by_depth(cell).items()
    }


def _by_cap(cell: CellRecord) -> dict[int, tuple[set[str], set[str]]]:
    """Split one run's claims into ``(delivered, surviving)`` at its cap.

    Returns:
        The two sets, keyed by the run's depth cap.
    """
    passing = set(cell.merged_passing)
    delivered: set[str] = set()
    for claims in _claims_by_depth(cell).values():
        delivered |= claims
    return {cell.depth_cap: (delivered, delivered & passing)}


def _curve(
    cells: Iterable[CellRecord], *, key: Splitter, run_bucket: RunBucket
) -> tuple[DepthPoint, ...]:
    """Fold every run into one point per ``(bin, arm)``.

    Claims are summed across repetitions rather than averaged over them: a
    repetition that produced more leaf work carries more weight, which is what
    a rate over work rather than a mean of rates means.

    Returns:
        The points, ordered by depth then arm.
    """
    delivered: dict[tuple[int, Arm], int] = defaultdict(int)
    surviving: dict[tuple[int, Arm], int] = defaultdict(int)
    counted: dict[tuple[int, Arm], int] = defaultdict(int)
    booked: dict[tuple[int, Arm], int] = defaultdict(int)
    cost: dict[tuple[int, Arm], float] = defaultdict(float)
    tokens: dict[tuple[int, Arm], int] = defaultdict(int)
    attempts: dict[tuple[int, Arm], int] = defaultdict(int)
    for cell in cells:
        if cell.achieved_depth is None:
            continue
        split = key(cell)
        for bucket, (claimed, kept) in split.items():
            slot = (bucket, cell.arm)
            delivered[slot] += len(claimed)
            surviving[slot] += len(kept)
            counted[slot] += 1
        # A run's cost belongs to the run, so it is booked once, in the bucket
        # this curve puts the run itself in. Booking it in every bucket a leaf
        # landed in would multiply the sweep's spend by the tree's height, and
        # booking it in a bucket this curve does not use would mint a phantom
        # point carrying spend and no work.
        #
        # Counted separately from *counted* because on the achieved-depth curve
        # the two are different populations: a run contributes claims to every
        # level its leaves sat at and books its spend at one. Rendering the pair
        # as one column made spend-per-run a ratio across two populations, and a
        # run whose leaves ALL failed books spend while contributing no claims
        # at all, which is precisely the deep failed run the sweep exists to
        # measure.
        run_slot = (run_bucket(cell), cell.arm)
        booked[run_slot] += 1
        cost[run_slot] += cell.total_cost
        tokens[run_slot] += cell.total_tokens
        attempts[run_slot] += cell.total_attempts
    slots = set(delivered) | set(booked)
    return tuple(
        DepthPoint(
            depth=depth,
            arm=arm,
            delivered_claims=delivered[(depth, arm)],
            surviving_claims=surviving[(depth, arm)],
            cells=counted[(depth, arm)],
            runs=booked[(depth, arm)],
            cost=cost[(depth, arm)],
            tokens=tokens[(depth, arm)],
            attempts=attempts[(depth, arm)],
        )
        for depth, arm in sorted(slots, key=lambda slot: (slot[0], slot[1].value))
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
