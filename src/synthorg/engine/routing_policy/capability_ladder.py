"""Capability-ladder helpers for stakes-aware routing.

The canonical rungs (``basic`` < ``capable`` < ``expert``) are the routing
vocabulary. The ladder and rank live in :mod:`synthorg.core.types` (the single
source shared with the provider routing resolver); this module adds the
stakes-routing helpers layered on top.
"""

from synthorg.core.types import (
    CAPABILITY_LADDER,
    CapabilityLevel,
    capability_meets,
    capability_rank,
)

# Weakest-first ladder. Re-exported for the stakes-routing modules that import
# it from here; the definition lives in ``core.types``.
LADDER = CAPABILITY_LADDER

# The adequacy check lives in ``core.types`` (the single source shared with the
# provider routing resolver); re-exported here under the stakes-routing name.
meets_required = capability_meets


def rank(capability: CapabilityLevel) -> int:
    """Return the weakest-first rank of *capability* (basic=0, expert=2)."""
    return capability_rank(capability)


def bump_one(capability: CapabilityLevel) -> CapabilityLevel:
    """Return the next rung up, or *capability* if already the strongest."""
    idx = min(rank(capability) + 1, len(LADDER) - 1)
    return LADDER[idx]
