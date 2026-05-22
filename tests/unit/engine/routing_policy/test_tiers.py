"""Unit tests for the model-tier ladder helpers."""

import pytest

from synthorg.core.types import ModelTier
from synthorg.engine.routing_policy.tiers import (
    TIER_LADDER,
    bump_one,
    higher_tier,
    tier_rank,
)


@pytest.mark.unit
class TestTierRank:
    """Cheapest-first rank: small=0, medium=1, large=2."""

    @pytest.mark.parametrize(
        ("tier", "rank"),
        [("small", 0), ("medium", 1), ("large", 2)],
    )
    def test_rank(self, tier: ModelTier, rank: int) -> None:
        assert tier_rank(tier) == rank

    def test_ladder_is_cheapest_first(self) -> None:
        assert TIER_LADDER == ("small", "medium", "large")


@pytest.mark.unit
class TestHigherTier:
    """``higher_tier`` returns the stronger of two tiers, order-independent."""

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("small", "large", "large"),
            ("large", "small", "large"),
            ("small", "medium", "medium"),
            ("medium", "small", "medium"),
            ("medium", "large", "large"),
            ("medium", "medium", "medium"),
            ("large", "large", "large"),
            ("small", "small", "small"),
        ],
    )
    def test_returns_stronger(
        self,
        a: ModelTier,
        b: ModelTier,
        expected: ModelTier,
    ) -> None:
        assert higher_tier(a, b) == expected


@pytest.mark.unit
class TestBumpOne:
    """``bump_one`` steps up one tier and saturates at the strongest."""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            ("small", "medium"),
            ("medium", "large"),
            ("large", "large"),
        ],
    )
    def test_bump(self, tier: ModelTier, expected: ModelTier) -> None:
        assert bump_one(tier) == expected
