"""Tests for the trust strategy factory."""

import pytest

from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.security.trust.config import (
    MilestoneCriteria,
    TrustConfig,
    TrustThreshold,
    WeightedTrustWeights,
)
from synthorg.security.trust.enums import TrustStrategyType
from synthorg.security.trust.factory import build_trust_strategy
from synthorg.security.trust.milestone_strategy import MilestoneTrustStrategy
from synthorg.security.trust.per_category_strategy import PerCategoryTrustStrategy
from synthorg.security.trust.weighted_strategy import WeightedTrustStrategy


@pytest.mark.unit
class TestBuildTrustStrategy:
    """``build_trust_strategy`` dispatches by ``TrustConfig.strategy``."""

    def test_disabled_strategy_returns_none(self) -> None:
        """DISABLED is the conditional-instantiation signal."""
        assert build_trust_strategy(TrustConfig()) is None
        assert (
            build_trust_strategy(
                TrustConfig(strategy=TrustStrategyType.DISABLED),
            )
            is None
        )

    def test_weighted_strategy(self) -> None:
        config = TrustConfig(
            strategy=TrustStrategyType.WEIGHTED,
            weights=WeightedTrustWeights(),
            promotion_thresholds={
                "standard_to_elevated": TrustThreshold(
                    score=0.9,
                    requires_human_approval=True,
                ),
            },
        )
        strategy = build_trust_strategy(config)
        assert isinstance(strategy, WeightedTrustStrategy)

    def test_per_category_strategy(self) -> None:
        config = TrustConfig(
            strategy=TrustStrategyType.PER_CATEGORY,
            initial_category_levels={"file_io": ToolAccessLevel.STANDARD},
        )
        strategy = build_trust_strategy(config)
        assert isinstance(strategy, PerCategoryTrustStrategy)

    def test_milestone_strategy(self) -> None:
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            milestones={
                "sandboxed_to_restricted": MilestoneCriteria(
                    tasks_completed=5,
                    quality_score_min=7.0,
                ),
            },
        )
        strategy = build_trust_strategy(config)
        assert isinstance(strategy, MilestoneTrustStrategy)
