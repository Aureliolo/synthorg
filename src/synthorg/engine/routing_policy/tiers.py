"""Model-tier ladder helpers for stakes-aware routing.

The canonical tiers (``small`` < ``medium`` < ``large``) are the routing tier
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

# Cheapest-first ladder. Re-exported for the stakes-routing modules that import
# it from here; the definition lives in ``core.types``.
TIER_LADDER = CAPABILITY_LADDER

# The tier-adequacy check lives in ``core.types`` (the single source shared with
# the provider routing resolver); re-exported here under the stakes-routing name.
meets_required = capability_meets


def tier_rank(tier: CapabilityLevel) -> int:
    """Return the cheapest-first rank of *tier* (small=0, large=2)."""
    return capability_rank(tier)


def higher_tier(a: CapabilityLevel, b: CapabilityLevel) -> CapabilityLevel:
    """Return the stronger (more expensive) of two tiers."""
    return a if tier_rank(a) >= tier_rank(b) else b


def bump_one(tier: CapabilityLevel) -> CapabilityLevel:
    """Return the next stronger tier, or *tier* if already the strongest."""
    idx = min(tier_rank(tier) + 1, len(TIER_LADDER) - 1)
    return TIER_LADDER[idx]
