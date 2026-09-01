# module-kind: code
"""The headline axis: what one solved requirement cost, with an interval.

The two fraction curves in :mod:`evals.recursion_depth.score` say what was
solved and where the work came from. Neither says what it cost, and neither
carries an interval, so a reader holding two pooled fractions cannot tell a
real gap from the spread five repetitions would show anyway. Published harness
comparisons have measured a forty-fold cost separation between loops while
every pairwise pass-rate interval crossed zero: the loops were separable on
cost and not on correctness, and a report ranking on correctness alone would
have ranked two things it could not tell apart.

The ratio is POOLED the way every other bucket figure is (all tokens over all
solved requirements), so a repetition that did more work weighs more. The
interval is a seeded percentile bootstrap over the bucket's runs: resample the
runs with replacement, pool each resample, take the outer percentiles. Seeded
from the runs themselves rather than from a clock or a flag, so a re-score of
the same journal reproduces the interval to the digit and two people scoring
one recording cannot publish two different ones.

A resample that solved nothing has an infinite cost per solved requirement,
and that is carried rather than dropped: dropping it would report a bounded
interval over a bucket whose runs sometimes solve nothing, which is the one
bucket whose cost genuinely has no ceiling.
"""

import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Final

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import (
    BOOTSTRAP_RESAMPLES,
    MIN_CELLS_FOR_INTERVAL,
    CellRecord,
    TokensPerSolvedPoint,
)

#: The two percentiles a 95% interval sits between.
_LOW_QUANTILE: Final[float] = 0.025
_HIGH_QUANTILE: Final[float] = 0.975

#: An overlap needs two things to overlap.
_ARMS_TO_COMPARE: Final[int] = 2

#: One run, reduced to the two numbers the ratio is over.
type _Run = tuple[int, int]

#: Says which bucket a run belongs in.
type _Bucket = Callable[[CellRecord], int]


def tokens_per_solved_by_achieved_depth(
    cells: Iterable[CellRecord],
) -> tuple[TokensPerSolvedPoint, ...]:
    """Bin cost per solved requirement on the depth each tree reached.

    Args:
        cells: The measured runs.

    Returns:
        One point per ``(depth, arm)``, ascending, on the axis the two
        fraction curves use.
    """
    return _curve(cells, bucket=lambda cell: cell.achieved_depth or 0)


def tokens_per_solved_by_depth_cap(
    cells: Iterable[CellRecord],
) -> tuple[TokensPerSolvedPoint, ...]:
    """Bin cost per solved requirement on the cap the run was allowed.

    Args:
        cells: The measured runs.

    Returns:
        One point per ``(cap, arm)``, ascending.
    """
    return _curve(cells, bucket=lambda cell: cell.depth_cap)


def indistinguishable_depths(
    points: Iterable[TokensPerSolvedPoint],
) -> tuple[int, ...]:
    """The depths at which every arm's interval overlaps every other's.

    An overlap is the finding this axis exists to surface: two point
    estimates a factor apart, inside intervals that share ground, are two
    arms this recording cannot rank. A depth where any arm reports no
    interval is left out, because nothing can be said about an overlap with
    an absent bound.

    Args:
        points: The curve, either binning.

    Returns:
        The depths, ascending.
    """
    by_depth: dict[int, list[TokensPerSolvedPoint]] = defaultdict(list)
    for point in points:
        by_depth[point.depth].append(point)
    overlapping: list[int] = []
    for depth in sorted(by_depth):
        arms = by_depth[depth]
        bounds = [bound for bound in map(_bounds, arms) if bound is not None]
        if len(arms) < _ARMS_TO_COMPARE or len(bounds) != len(arms):
            continue
        if max(low for low, _ in bounds) <= min(high for _, high in bounds):
            overlapping.append(depth)
    return tuple(overlapping)


def _bounds(point: TokensPerSolvedPoint) -> tuple[float, float] | None:
    """One point's interval as a closed pair, infinity for an open top.

    Returns:
        ``(low, high)``, or ``None`` when the point reports no interval.
    """
    if point.ci_low is None:
        return None
    return point.ci_low, math.inf if point.ci_high is None else point.ci_high


def _curve(
    cells: Iterable[CellRecord], *, bucket: _Bucket
) -> tuple[TokensPerSolvedPoint, ...]:
    """Fold every run into one point per ``(bin, arm)``.

    Returns:
        The points, ordered by depth then arm.
    """
    runs: dict[tuple[int, Arm], list[_Run]] = defaultdict(list)
    for cell in cells:
        if cell.achieved_depth is None:
            continue
        # DISTINCT ids, for the reason the specification curve takes them:
        # `merged_passing` permits repeats and a requirement is solved once.
        runs[(bucket(cell), cell.arm)].append(
            (cell.total_tokens, len(set(cell.merged_passing)))
        )
    return tuple(
        _point(depth, arm, runs[(depth, arm)])
        for depth, arm in sorted(runs, key=lambda slot: (slot[0], slot[1].value))
    )


def _point(depth: int, arm: Arm, runs: Sequence[_Run]) -> TokensPerSolvedPoint:
    """Build one bucket's point from the runs it holds.

    Returns:
        The point.
    """
    low, high, unbounded = _interval(runs)
    return TokensPerSolvedPoint(
        depth=depth,
        arm=arm,
        tokens=sum(tokens for tokens, _ in runs),
        solved=sum(solved for _, solved in runs),
        cells=len(runs),
        ci_low=low,
        ci_high=high,
        unbounded_above=unbounded,
    )


def _interval(runs: Sequence[_Run]) -> tuple[float | None, float | None, bool]:
    """The 95% percentile bootstrap interval over *runs*.

    Returns:
        ``(low, high, unbounded_above)``: both bounds absent below the run
        floor, or when even the lower percentile solved nothing; the upper
        bound absent and the flag set when only the upper percentile did.
    """
    if len(runs) < MIN_CELLS_FOR_INTERVAL:
        return None, None, False
    # A resampling draw, not a secret: the seed is public by design so a
    # re-score reproduces the interval.
    rng = random.Random(_seed(runs))  # noqa: S311
    ratios = sorted(
        _pooled(rng.choices(runs, k=len(runs))) for _ in range(BOOTSTRAP_RESAMPLES)
    )
    low = ratios[_index(_LOW_QUANTILE)]
    high = ratios[_index(_HIGH_QUANTILE)]
    if math.isinf(low):
        return None, None, True
    return low, (None if math.isinf(high) else high), math.isinf(high)


def _pooled(draw: Sequence[_Run]) -> float:
    """One resample's pooled ratio, infinite when it solved nothing.

    Returns:
        Tokens over solved requirements.
    """
    tokens = sum(count for count, _ in draw)
    solved = sum(count for _, count in draw)
    return tokens / solved if solved else math.inf


def _index(quantile: float) -> int:
    """Where *quantile* sits in the sorted resamples.

    Returns:
        The index.
    """
    return round(quantile * (BOOTSTRAP_RESAMPLES - 1))


def _seed(runs: Sequence[_Run]) -> str:
    """A seed that is a pure function of the runs, whatever order they came in.

    Returns:
        The seed text.
    """
    return "|".join(f"{tokens}/{solved}" for tokens, solved in sorted(runs))


__all__ = [
    "indistinguishable_depths",
    "tokens_per_solved_by_achieved_depth",
    "tokens_per_solved_by_depth_cap",
]
