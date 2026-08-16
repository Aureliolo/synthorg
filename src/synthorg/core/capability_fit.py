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

from collections.abc import Callable, Iterator, Sequence
from typing import Literal

CapabilityFit = Literal["match", "higher", "lower"]


def bands_by_fit[T](
    candidates: Sequence[T],
    rank_of: Callable[[T], int],
    required_rank: int,
) -> Iterator[tuple[tuple[T, ...], CapabilityFit]]:
    """Yield every non-empty band, best fit first.

    The whole ladder rather than its head, because a band is a set of
    candidates and not an answer: a caller that then scores them can find
    nobody usable in the best-fitting band while a perfectly good candidate
    waits one rung up. Offering only the head made that outcome
    indistinguishable from an empty pool.

    Rungs are walked one at a time in both directions, for the reason the
    single-band helper already gave: a candidate two rungs over is a worse fit
    AND a more expensive one, so it is offered only once everything between it
    and the requirement has been.

    Yields:
        Each non-empty band with its fit label, in preference order: the exact
        rung, then each rung above ascending, then each rung below descending.
    """
    ranked = [(rank_of(c), c) for c in candidates]
    exact = tuple(c for rank, c in ranked if rank == required_rank)
    if exact:
        yield exact, "match"
    for rank in sorted({r for r, _ in ranked if r > required_rank}):
        yield tuple(c for r, c in ranked if r == rank), "higher"
    for rank in sorted({r for r, _ in ranked if r < required_rank}, reverse=True):
        yield tuple(c for r, c in ranked if r == rank), "lower"


def partition_by_fit[T](
    candidates: Sequence[T],
    rank_of: Callable[[T], int],
    required_rank: int,
) -> tuple[tuple[T, ...], CapabilityFit] | None:
    """Return the best-fitting band of *candidates*, and how it fits.

    The head of :func:`bands_by_fit`, for callers that take the first band and
    have nothing to re-try with.

    Args:
        candidates: The pool to choose from.
        rank_of: Reads a candidate's capability rank.
        required_rank: The rank the work demands.

    Returns:
        The non-empty band closest to the requirement (exact matches, else the
        nearest rung above, else the nearest rung below) with its fit label, or
        ``None`` when the pool is empty.
    """
    return next(iter(bands_by_fit(candidates, rank_of, required_rank)), None)


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


__all__ = ["CapabilityFit", "bands_by_fit", "best_by_fit", "partition_by_fit"]
