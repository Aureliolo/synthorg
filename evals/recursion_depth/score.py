# module-kind: code
"""The measurement, on two axes that answer two different questions.

After the root merge, the held-out oracle runs against the final tree and says
which of the specification's requirements it satisfies. From that one grading
this module derives both curves, per achieved depth and per cap, for each arm.

**SPECIFICATION** is that count over the specification's own requirement count.
Its denominator is identical for every cell and cannot empty, so every run
produces a point and the two arms are comparable at every depth, including
where a cell's leaves all failed. What it does not say is where the work came
from: a tree scoring well because the merging agent rebuilt it and one scoring
well because its leaves' work survived are the same number there.

**SURVIVAL** is the question this experiment was built around: of the
requirements the DELIVERED leaves claimed, how many the merged tree still
satisfies. Delivery rather than standalone correctness gates the denominator,
because a leaf's own tree usually cannot run the spec oracle at all (at depth a
unit is one function and nothing above it exists yet), so requiring a standalone
pass would empty the denominator exactly where the curve is most interesting.
This denominator is leaf work, so it CAN empty, and an empty one is reported as
an absent point rather than a zero: nothing was measured there, and a zero says
everything was lost.

The two are plotted together because the pair coming apart IS the finding, and
neither replaces the other.

**Depth is the depth the tree ACHIEVED.** The cap is what the run was allowed;
the planner decides what it uses. Binning on the cap makes caps the planner
never reached look like measured points, so the primary curves bin each run on
the depth it reached and the cap curves are reported beside them with the
histogram that says how much of the sweep was real.

**SPREAD** is a third view over the same two numbers, and it exists because
both curves POOL a bucket's repetitions. Pooling is the right shape for a rate
over work and it cannot answer the question a repeated cap is recorded to
answer: whether a low point is one bad draw or a real drop. So each bucket also
reports the range and the middle of its runs, per metric, and a reader who
wants the runs themselves finds one row each in ``cells``.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from statistics import median_low

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import (
    CellRecord,
    DepthPoint,
    DepthSpread,
    SurvivalPoint,
)

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
    the two agree anyway, so the choice does not move the result.

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
        # One run, one bucket, for the fraction AND the spend: a run's claims
        # and its spend are counted at the same granularity, so a second count
        # would always equal the first and could never disagree with it.
        slot = (bucket(cell), cell.arm)
        required[slot] += requirement_count
        # DISTINCT ids, because the denominator counts each requirement once
        # and `merged_passing` is a sequence that permits repeats: a cell
        # listing R01 twice would satisfy two of the one requirement, which
        # either inflates the fraction or trips the point's own subset check.
        satisfied[slot] += len(set(cell.merged_passing))
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


def survival_by_achieved_depth(
    cells: Iterable[CellRecord],
) -> tuple[SurvivalPoint, ...]:
    """Bin leaf-work survival on the depth each tree actually reached.

    Args:
        cells: The measured runs.

    Returns:
        One point per ``(depth, arm)`` that any run reached, ascending, on the
        same axis :func:`curve_by_achieved_depth` uses.
    """
    return _survival(cells, bucket=_achieved_bucket)


def survival_by_depth_cap(cells: Iterable[CellRecord]) -> tuple[SurvivalPoint, ...]:
    """Bin leaf-work survival on the ``max_depth`` cap the run was allowed.

    Args:
        cells: The measured runs.

    Returns:
        One point per ``(cap, arm)``, ascending.
    """
    return _survival(cells, bucket=_cap_bucket)


def _delivered_claims(cell: CellRecord) -> set[RequirementId]:
    """The requirements this run's DELIVERED leaves claimed.

    A set, because two leaves claiming one requirement is a planner producing
    overlapping units rather than more work, and counting it twice weights the
    bucket by how repetitive the plan was.

    Returns:
        The claimed requirement ids.
    """
    return {
        identifier
        for leaf in cell.leaves
        if leaf.delivered
        for identifier in leaf.claimed
    }


def _survival(
    cells: Iterable[CellRecord], *, bucket: RunBucket
) -> tuple[SurvivalPoint, ...]:
    """Fold every run into one survival point per ``(bin, arm)``.

    Summed across repetitions rather than averaged over them: a repetition that
    produced more leaf work carries more weight, which is what a rate over work
    rather than a mean of rates means, and it is what keeps a bucket whose
    every leaf failed reporting an absent point rather than dragging a mean.

    Returns:
        The points, ordered by depth then arm.
    """
    delivered: dict[tuple[int, Arm], int] = defaultdict(int)
    surviving: dict[tuple[int, Arm], int] = defaultdict(int)
    counted: dict[tuple[int, Arm], int] = defaultdict(int)
    for cell in cells:
        if cell.achieved_depth is None:
            continue
        slot = (bucket(cell), cell.arm)
        claimed = _delivered_claims(cell)
        delivered[slot] += len(claimed)
        surviving[slot] += len(claimed & set(cell.merged_passing))
        counted[slot] += 1
    return tuple(
        SurvivalPoint(
            depth=depth,
            arm=arm,
            delivered_claims=delivered[(depth, arm)],
            surviving_claims=surviving[(depth, arm)],
            cells=counted[(depth, arm)],
        )
        for depth, arm in sorted(counted, key=lambda slot: (slot[0], slot[1].value))
    )


def spread_by_achieved_depth(
    cells: Iterable[CellRecord], *, requirement_count: int
) -> tuple[DepthSpread, ...]:
    """Report how much each bucket's repetitions disagreed, by depth reached.

    Args:
        cells: The measured runs.
        requirement_count: The specification's own requirement count, for the
            subset check each run's satisfied count is held to.

    Returns:
        One row per ``(depth, arm)``, on the axis
        :func:`curve_by_achieved_depth` uses.
    """
    return _spread(cells, bucket=_achieved_bucket, requirement_count=requirement_count)


def spread_by_depth_cap(
    cells: Iterable[CellRecord], *, requirement_count: int
) -> tuple[DepthSpread, ...]:
    """The same, binned on the ``max_depth`` cap the run was allowed.

    Args:
        cells: The measured runs.
        requirement_count: The specification's own requirement count.

    Returns:
        One row per ``(cap, arm)``, ascending.
    """
    return _spread(cells, bucket=_cap_bucket, requirement_count=requirement_count)


def _run_survival(cell: CellRecord) -> float | None:
    """One run's own leaf-work survival rate.

    Per RUN rather than per bucket, which is what makes a spread a spread. The
    absent case is the same one :class:`SurvivalPoint` states and it has to be
    kept absent here too: a run whose delivered leaves claimed nothing has no
    rate, and folding it in as a zero reports a collapse nobody measured.

    Returns:
        The rate, or ``None`` when this run attributed nothing.
    """
    claimed = _delivered_claims(cell)
    if not claimed:
        return None
    return len(claimed & set(cell.merged_passing)) / len(claimed)


def _spread(
    cells: Iterable[CellRecord], *, bucket: RunBucket, requirement_count: int
) -> tuple[DepthSpread, ...]:
    """Fold every run into one range per ``(bin, arm)``.

    Ranged over runs rather than over pooled operands, and the median is the
    LOW one: it is always a value some run actually recorded, so a bucket of
    two never reports a figure that describes neither of them.

    Returns:
        The rows, ordered by depth then arm.
    """
    satisfied: dict[tuple[int, Arm], list[int]] = defaultdict(list)
    survival: dict[tuple[int, Arm], list[float]] = defaultdict(list)
    counted: dict[tuple[int, Arm], int] = defaultdict(int)
    for cell in cells:
        if cell.achieved_depth is None:
            continue
        slot = (bucket(cell), cell.arm)
        # DISTINCT ids, for the reason `_curve` takes them: `merged_passing`
        # permits repeats and the denominator counts each requirement once.
        satisfied[slot].append(len(set(cell.merged_passing)))
        rate = _run_survival(cell)
        if rate is not None:
            survival[slot].append(rate)
        counted[slot] += 1
    return tuple(
        _row(
            depth,
            arm,
            satisfied[(depth, arm)],
            survival[(depth, arm)],
            cells=counted[(depth, arm)],
            requirement_count=requirement_count,
        )
        for depth, arm in sorted(counted, key=lambda slot: (slot[0], slot[1].value))
    )


def _row(
    depth: int,
    arm: Arm,
    satisfied: Sequence[int],
    survival: Sequence[float],
    *,
    cells: int,
    requirement_count: int,
) -> DepthSpread:
    """Build one bucket's row from the runs it holds.

    Returns:
        The row.
    """
    return DepthSpread(
        depth=depth,
        arm=arm,
        cells=cells,
        required=requirement_count,
        satisfied_min=min(satisfied),
        satisfied_median=median_low(satisfied),
        satisfied_max=max(satisfied),
        survival_min=min(survival) if survival else None,
        survival_median=median_low(survival) if survival else None,
        survival_max=max(survival) if survival else None,
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
    "spread_by_achieved_depth",
    "spread_by_depth_cap",
    "survival_by_achieved_depth",
    "survival_by_depth_cap",
]
