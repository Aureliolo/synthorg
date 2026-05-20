"""Spearman rank correlation (tie-aware).

The non-executable judge calibration gate compares the judge's
per-anchor totals to the hand-scored totals: if the ranking agrees,
the judge can be trusted to order outputs even if its absolute scale
has drifted. This module implements Spearman rho via the standard
``Pearson on ranks`` definition, with fractional ranks for ties so
ties do not silently bias the coefficient toward 0.

scipy.stats.spearmanr would do the same; this in-repo implementation
keeps the eval spine dependency-free.
"""

from statistics import correlation
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

# Average-rank tie strategy is the conventional Spearman definition; we
# resolve ties by assigning each tied element the mean of the ranks it
# would have occupied.

MIN_PAIRS_FOR_CORRELATION: Final[int] = 3


def average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    """Return average ranks for *values* (1-indexed; ties share the mean).

    The output preserves input order: the i-th return element is the
    rank of ``values[i]``. Equal values receive the average of the
    contiguous rank positions they would have occupied if broken
    arbitrarily.
    """
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks: list[float] = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        # ranks i..j are tied; average rank is mean of (i+1)..(j+1)
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            original_position = indexed[k][0]
            ranks[original_position] = avg_rank
        i = j + 1
    return tuple(ranks)


def spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Compute Spearman rank correlation between *xs* and *ys*.

    Args:
        xs: First sample.
        ys: Second sample (same length as *xs*).

    Returns:
        Spearman rho in ``[-1.0, 1.0]``. Returns 1.0 when both samples
        are constant (degenerate but conventionally "perfectly agreed").

    Raises:
        ValueError: If the samples differ in length or are shorter
            than :data:`MIN_PAIRS_FOR_CORRELATION`. Two points have
            an undefined correlation; we refuse rather than emit a
            spurious value.
    """
    if len(xs) != len(ys):
        msg = f"spearman_rho: xs and ys length mismatch ({len(xs)} vs {len(ys)})"
        raise ValueError(msg)
    if len(xs) < MIN_PAIRS_FOR_CORRELATION:
        msg = (
            "spearman_rho: at least "
            f"{MIN_PAIRS_FOR_CORRELATION} paired samples required "
            f"(got {len(xs)})"
        )
        raise ValueError(msg)

    rx = average_ranks(xs)
    ry = average_ranks(ys)

    # Degenerate-case shortcut: when one (or both) samples are constant,
    # statistics.correlation raises StatisticsError. The conventional
    # interpretation is rho = 1.0 when both are constant, undefined
    # otherwise; we adopt that convention rather than propagate the
    # exception, since "all anchors got the same score" is a real
    # judge-degenerate signal worth surfacing as rho < gate.
    if len(set(rx)) == 1 and len(set(ry)) == 1:
        return 1.0
    if len(set(rx)) == 1 or len(set(ry)) == 1:
        return 0.0

    return correlation(rx, ry)


__all__ = ["MIN_PAIRS_FOR_CORRELATION", "average_ranks", "spearman_rho"]
