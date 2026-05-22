"""Model-tier ladder helpers for stakes-aware routing.

The canonical tiers (``small`` < ``medium`` < ``large``) double as the
model aliases the :class:`~synthorg.providers.routing.resolver.ModelResolver`
resolves, and as the keys
:class:`~synthorg.budget.benchmark_stub.StubBenchmarkScoreProvider`
scores. Cheapest-first order lets the policy pick the cheapest tier that
clears a quality floor.
"""

from typing import Final

from synthorg.core.types import ModelTier  # noqa: TC001 -- runtime annotations

# Cheapest-first ladder. Index doubles as the tier rank.
TIER_LADDER: Final[tuple[ModelTier, ...]] = ("small", "medium", "large")

_TIER_RANK: Final[dict[ModelTier, int]] = {
    tier: idx for idx, tier in enumerate(TIER_LADDER)
}


def tier_rank(tier: ModelTier) -> int:
    """Return the cheapest-first rank of *tier* (small=0, large=2)."""
    return _TIER_RANK[tier]


def higher_tier(a: ModelTier, b: ModelTier) -> ModelTier:
    """Return the stronger (more expensive) of two tiers."""
    return a if tier_rank(a) >= tier_rank(b) else b


def bump_one(tier: ModelTier) -> ModelTier:
    """Return the next stronger tier, or *tier* if already the strongest."""
    idx = min(tier_rank(tier) + 1, len(TIER_LADDER) - 1)
    return TIER_LADDER[idx]
