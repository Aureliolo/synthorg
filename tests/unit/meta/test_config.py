"""Unit tests for meta-loop configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.meta.config import (
    GuardChainConfig,
    PromptTuningConfig,
    RegressionConfig,
    RolloutConfig,
    RuleConfig,
    ScheduleConfig,
    SelfImprovementConfig,
)
from synthorg.meta.models import EvolutionMode, RolloutStrategyType

pytestmark = pytest.mark.unit


class TestSelfImprovementConfig:
    """Top-level config tests."""

    def test_safe_defaults(self) -> None:
        cfg = SelfImprovementConfig()
        assert cfg.enabled is False
        assert cfg.chief_of_staff_enabled is False
        assert cfg.config_tuning_enabled is True
        assert cfg.architecture_proposals_enabled is False
        assert cfg.prompt_tuning_enabled is False

    def test_frozen(self) -> None:
        cfg = SelfImprovementConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = True  # type: ignore[misc]

    def test_sub_configs_default_factory(self) -> None:
        cfg = SelfImprovementConfig()
        assert isinstance(cfg.schedule, ScheduleConfig)
        assert isinstance(cfg.rollout, RolloutConfig)
        assert isinstance(cfg.regression, RegressionConfig)
        assert isinstance(cfg.guards, GuardChainConfig)
        assert isinstance(cfg.rules, RuleConfig)
        assert isinstance(cfg.prompt_tuning, PromptTuningConfig)

    def test_analysis_sampling_defaults(self) -> None:
        # There is deliberately no baked analysis model: the pair lives in
        # ``self_improvement.analysis_model`` and is read per call.
        cfg = SelfImprovementConfig()
        assert cfg.analysis_temperature == 0.3
        assert cfg.analysis_max_tokens == 4000

    def test_temperature_bounds(self) -> None:
        SelfImprovementConfig(analysis_temperature=0.0)
        SelfImprovementConfig(analysis_temperature=2.0)
        with pytest.raises(ValidationError):
            SelfImprovementConfig(analysis_temperature=-0.1)
        with pytest.raises(ValidationError):
            SelfImprovementConfig(analysis_temperature=2.1)


class TestScheduleConfig:
    """Schedule config tests."""

    def test_defaults(self) -> None:
        cfg = ScheduleConfig()
        assert cfg.cycle_interval_hours == 168
        assert cfg.inflection_trigger_enabled is True

    def test_minimum_interval(self) -> None:
        ScheduleConfig(cycle_interval_hours=1)
        with pytest.raises(ValidationError):
            ScheduleConfig(cycle_interval_hours=0)


class TestRolloutConfig:
    """Rollout config tests."""

    def test_defaults(self) -> None:
        cfg = RolloutConfig()
        assert cfg.default_strategy == RolloutStrategyType.BEFORE_AFTER
        assert cfg.observation_window_hours == 48
        assert cfg.regression_check_interval_hours == 4

    def test_canary_strategy(self) -> None:
        cfg = RolloutConfig(
            default_strategy=RolloutStrategyType.CANARY,
        )
        assert cfg.default_strategy == RolloutStrategyType.CANARY


class TestRegressionConfig:
    """Regression config tests."""

    def test_defaults(self) -> None:
        cfg = RegressionConfig()
        assert cfg.quality_drop_threshold == 0.10
        assert cfg.cost_increase_threshold == 0.20
        assert cfg.error_rate_increase_threshold == 0.15
        assert cfg.success_rate_drop_threshold == 0.10
        assert cfg.statistical_significance_level == 0.05
        assert cfg.min_data_points == 10

    def test_threshold_bounds(self) -> None:
        RegressionConfig(quality_drop_threshold=0.0)
        RegressionConfig(quality_drop_threshold=1.0)
        with pytest.raises(ValidationError):
            RegressionConfig(quality_drop_threshold=-0.01)
        with pytest.raises(ValidationError):
            RegressionConfig(quality_drop_threshold=1.01)

    def test_significance_bounds(self) -> None:
        RegressionConfig(statistical_significance_level=0.001)
        RegressionConfig(statistical_significance_level=0.5)
        with pytest.raises(ValidationError):
            RegressionConfig(statistical_significance_level=0.0001)

    def test_min_data_points_minimum(self) -> None:
        RegressionConfig(min_data_points=2)
        with pytest.raises(ValidationError):
            RegressionConfig(min_data_points=1)


class TestGuardChainConfig:
    """Guard chain config tests."""

    def test_defaults(self) -> None:
        cfg = GuardChainConfig()
        assert cfg.proposal_rate_limit == 10
        assert cfg.rate_limit_window_hours == 24


class TestRuleConfig:
    """Rule config tests."""

    def test_defaults(self) -> None:
        cfg = RuleConfig()
        assert cfg.disabled_rules == ()
        assert cfg.custom_rule_modules == ()

    def test_disable_rules(self) -> None:
        cfg = RuleConfig(
            disabled_rules=("quality_declining", "budget_overrun"),
        )
        assert len(cfg.disabled_rules) == 2


class TestPromptTuningConfig:
    """Prompt tuning config tests."""

    def test_defaults(self) -> None:
        cfg = PromptTuningConfig()
        assert cfg.default_evolution_mode == EvolutionMode.ORG_WIDE
        assert len(cfg.allowed_modes) == 3

    def test_restricted_modes(self) -> None:
        cfg = PromptTuningConfig(
            allowed_modes=("org_wide",),
        )
        assert len(cfg.allowed_modes) == 1
