"""Tests for HumanOnlyPromotionStrategy."""

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.security.autonomy.change_strategy import HumanOnlyPromotionStrategy
from synthorg.security.autonomy.protocol import AutonomyChangeStrategy


class TestPromotion:
    """Promotion is always denied in human-only strategy."""

    @pytest.mark.unit
    def test_promotion_denied(self) -> None:
        strategy = HumanOnlyPromotionStrategy()
        result = strategy.request_promotion("agent-1", AutonomyLevel.FULL)
        assert result is False

    @pytest.mark.unit
    @pytest.mark.parametrize("target", list(AutonomyLevel))
    def test_all_promotions_denied(self, target: AutonomyLevel) -> None:
        strategy = HumanOnlyPromotionStrategy()
        assert strategy.request_promotion("agent-x", target) is False


class TestNothingLowersAGrant:
    """An operator owns the autonomy level, so the runtime never moves it down.

    Pinned rather than left to the absence of a caller, because that absence
    is what the machinery had before: a downgrade path existed, read as a
    control to anyone auditing the tree, and ran for nobody. A future
    reintroduction has to answer who is permitted to lower an operator's grant
    unasked, and deleting these assertions is where that answer gets written.
    """

    @pytest.mark.unit
    def test_the_strategy_offers_no_way_down(self) -> None:
        strategy = HumanOnlyPromotionStrategy()

        assert not hasattr(strategy, "auto_downgrade")
        assert not hasattr(strategy, "request_recovery")

    @pytest.mark.unit
    def test_the_seam_asks_only_about_promotion(self) -> None:
        """The protocol is the shape an alternative strategy is written to."""
        declared = {
            name for name in vars(AutonomyChangeStrategy) if not name.startswith("_")
        }

        assert declared == {"request_promotion"}
