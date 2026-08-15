"""Choosing the candidate whose capability best fits what work demands.

One rule, used by every selection the org makes: an exact match first, then
the nearest rung above, then the nearest rung below. Preferring an exact match
over a stronger one is the standing cost discipline (it picks the cheapest
candidate that can do the work, on every selection rather than only past a
budget threshold), and going below is the last resort rather than a refusal,
because a weaker agent still does the work and every deliverable still passes
the completion gates.

Whether going below is ALLOWED is not decided here: the capability policy
answers that from the work's stakes, and a caller that refuses passes only
sanctioned candidates in. This module just orders what it is given.
"""

from collections.abc import Callable, Sequence
from typing import Literal

CapabilityFit = Literal["match", "higher", "lower"]


def partition_by_fit[T](
    candidates: Sequence[T],
    rank_of: Callable[[T], int],
    required_rank: int,
) -> tuple[tuple[T, ...], CapabilityFit] | None:
    """Return the best-fitting band of *candidates*, and how it fits.

    Args:
        candidates: The pool to choose from.
        rank_of: Reads a candidate's capability rank.
        required_rank: The rank the work demands.

    Returns:
        The non-empty band closest to the requirement (exact matches, else
        every candidate above, else every candidate below) with its fit
        label, or ``None`` when the pool is empty.
    """
    if not candidates:
        return None
    exact = tuple(c for c in candidates if rank_of(c) == required_rank)
    if exact:
        return exact, "match"
    above = tuple(c for c in candidates if rank_of(c) > required_rank)
    if above:
        # The nearest rung above, not every rung above: a candidate two rungs
        # over is a worse fit AND a more expensive one, so it only wins when
        # nothing sits between it and the requirement.
        nearest = min(rank_of(c) for c in above)
        return tuple(c for c in above if rank_of(c) == nearest), "higher"
    below = tuple(c for c in candidates if rank_of(c) < required_rank)
    nearest_below = max(rank_of(c) for c in below)
    return tuple(c for c in below if rank_of(c) == nearest_below), "lower"


def best_by_fit[T](
    candidates: Sequence[T],
    rank_of: Callable[[T], int],
    required_rank: int,
    tie_break: Callable[[T], str],
) -> tuple[T, CapabilityFit] | None:
    """Return the single best-fitting candidate, ties broken deterministically.

    Args:
        candidates: The pool to choose from.
        rank_of: Reads a candidate's capability rank.
        required_rank: The rank the work demands.
        tie_break: Stable ordering key within the winning band, so the same
            pool always resolves the same way.

    Returns:
        The chosen candidate with how its capability fit, or ``None`` when
        the pool is empty.
    """
    banded = partition_by_fit(candidates, rank_of, required_rank)
    if banded is None:
        return None
    band, fit = banded
    return min(band, key=tie_break), fit


__all__ = ["CapabilityFit", "best_by_fit", "partition_by_fit"]
