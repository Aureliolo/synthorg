"""Unit tests for cost tier resolution."""

import pytest

from synthorg.engine.strategy.models import (
    CostTierPreset,
    ImpactScore,
    StrategyConfig,
)
from synthorg.engine.strategy.tiers import (
    ProgressiveTierResolver,
)


class TestProgressiveTierResolver:
    """Tests for ProgressiveTierResolver."""

    @pytest.mark.unit
    def test_no_impact_falls_back_to_config(self) -> None:
        config = StrategyConfig(cost_tier=CostTierPreset.MODERATE)
        resolver = ProgressiveTierResolver()
        result = resolver.resolve(impact=None, config=config)
        assert result == CostTierPreset.MODERATE

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("composite", "expected"),
        [
            (0.1, CostTierPreset.MINIMAL),
            (0.39, CostTierPreset.MINIMAL),
            (0.4, CostTierPreset.MODERATE),
            (0.5, CostTierPreset.MODERATE),
            (0.69, CostTierPreset.MODERATE),
            (0.7, CostTierPreset.GENEROUS),
            (0.95, CostTierPreset.GENEROUS),
        ],
    )
    def test_threshold_boundaries(
        self,
        composite: float,
        expected: CostTierPreset,
    ) -> None:
        config = StrategyConfig()
        impact = ImpactScore(
            dimensions={},
            composite=composite,
            tier=CostTierPreset.MODERATE,  # ignored by resolver
        )
        resolver = ProgressiveTierResolver()
        result = resolver.resolve(impact=impact, config=config)
        assert result == expected
