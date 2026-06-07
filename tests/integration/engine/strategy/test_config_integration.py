"""Integration tests for strategy config in RootConfig."""

import pytest
from pydantic import ValidationError

from synthorg.config.schema import RootConfig
from synthorg.engine.strategy.models import CostTierPreset, StrategyConfig
from synthorg.hr.strategy_mode import StrategicOutputMode


class TestRootConfigStrategy:
    """Tests for strategy section in RootConfig."""

    @pytest.mark.integration
    def test_default_strategy_config(self) -> None:
        rc = RootConfig(company_name="Test Corp")
        assert isinstance(rc.strategy, StrategyConfig)
        assert rc.strategy.output_mode == StrategicOutputMode.ADVISOR
        assert rc.strategy.cost_tier == CostTierPreset.MODERATE

    @pytest.mark.integration
    def test_strategy_from_dict(self) -> None:
        rc = RootConfig(
            company_name="Test Corp",
            strategy={  # type: ignore[arg-type]
                "output_mode": "decision_maker",
                "cost_tier": "generous",
                "default_lenses": ["contrarian"],
            },
        )
        assert rc.strategy.output_mode == StrategicOutputMode.DECISION_MAKER
        assert rc.strategy.cost_tier == CostTierPreset.GENEROUS
        assert rc.strategy.default_lenses == ("contrarian",)

    @pytest.mark.integration
    def test_strategy_with_progressive_weights(self) -> None:
        rc = RootConfig(
            company_name="Test Corp",
            strategy={  # type: ignore[arg-type]
                "progressive": {
                    "weights": {
                        "budget_impact": 0.3,
                        "authority_level": 0.1,
                        "decision_type": 0.1,
                        "reversibility": 0.2,
                        "blast_radius": 0.1,
                        "time_horizon": 0.1,
                        "strategic_alignment": 0.1,
                    },
                    "thresholds": {
                        "moderate": 0.3,
                        "generous": 0.6,
                    },
                },
            },
        )
        assert rc.strategy.progressive.weights.budget_impact == 0.3
        assert rc.strategy.progressive.thresholds.moderate == 0.3

    @pytest.mark.integration
    def test_strategy_with_context(self) -> None:
        rc = RootConfig(
            company_name="Test Corp",
            strategy={  # type: ignore[arg-type]
                "context": {
                    "maturity_stage": "seed",
                    "industry": "fintech",
                    "competitive_position": "niche",
                },
            },
        )
        assert rc.strategy.context.maturity_stage == "seed"
        assert rc.strategy.context.industry == "fintech"

    @pytest.mark.integration
    def test_strategy_frozen(self) -> None:
        rc = RootConfig(company_name="Test Corp")
        with pytest.raises(ValidationError):
            rc.strategy = StrategyConfig()  # type: ignore[misc]

    @pytest.mark.integration
    def test_empty_strategy_dict_uses_defaults(self) -> None:
        rc = RootConfig(company_name="Test Corp", strategy={})  # type: ignore[arg-type]
        assert rc.strategy.output_mode == StrategicOutputMode.ADVISOR
        assert len(rc.strategy.default_lenses) == 4
