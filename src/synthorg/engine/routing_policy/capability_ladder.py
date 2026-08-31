"""Capability-ladder helpers for the capability policy.

The canonical rungs (``basic`` < ``capable`` < ``expert``) are the routing
vocabulary. The ladder and rank live in :mod:`synthorg.core.types` (the single
source shared with the provider routing resolver); this module adds the two
helpers layered on top.
"""

from typing import Final

from synthorg.core.task_enums import Complexity
from synthorg.core.types import (
    CAPABILITY_LADDER,
    CapabilityLevel,
    capability_rank,
)

# Weakest-first ladder. Re-exported for the routing modules that import it
# from here; the definition lives in ``core.types``.
LADDER = CAPABILITY_LADDER


def rank(capability: CapabilityLevel) -> int:
    """Return the weakest-first rank of *capability* (basic=0, expert=2)."""
    return capability_rank(capability)


def bump_one(capability: CapabilityLevel) -> CapabilityLevel:
    """Return the next rung up, or *capability* if already the strongest."""
    idx = min(rank(capability) + 1, len(LADDER) - 1)
    return LADDER[idx]


SUBSTANTIAL_COMPLEXITIES: Final[frozenset[Complexity]] = frozenset(
    {Complexity.COMPLEX, Complexity.EPIC}
)
"""Complexities that raise the capability a piece of work demands.

SIMPLE and MEDIUM work is judged adequately at its stakes floor; past that
the shape of the work itself is the harder half of the problem.
"""
