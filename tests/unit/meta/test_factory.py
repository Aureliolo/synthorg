"""Unit tests for meta-loop factory module."""

import pytest
from pydantic import ValidationError

from synthorg.meta.chief_of_staff.learning import (
    BayesianConfidenceAdjuster,
    ExponentialMovingAverageAdjuster,
)
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.factory import (
    build_appliers,
    build_confidence_adjuster,
    build_guards,
    build_regression_detector,
    build_rollout_strategies,
    build_rule_engine,
    build_strategies,
)
from synthorg.meta.models import ProposalAltitude

pytestmark = pytest.mark.unit


class TestBuildRuleEngine:
    """Rule engine factory tests."""

    def test_all_rules_enabled_by_default(self) -> None:
        cfg = SelfImprovementConfig(enabled=True)
        engine = build_rule_engine(cfg)
        assert engine.rule_count == 10

    def test_disabled_rules_excluded(self) -> None:
        from synthorg.meta.config import RuleConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            rules=RuleConfig(
                disabled_rules=("quality_declining", "budget_overrun"),
            ),
        )
        engine = build_rule_engine(cfg)
        assert engine.rule_count == 8
        assert "quality_declining" not in engine.rule_names
        assert "budget_overrun" not in engine.rule_names

    def test_disable_all_rules(self) -> None:
        from synthorg.meta.config import RuleConfig

        all_names = (
            "quality_declining",
            "success_rate_drop",
            "budget_overrun",
            "coordination_cost_ratio",
            "coordination_overhead",
            "straggler_bottleneck",
            "redundancy",
            "scaling_failure",
            "error_spike",
            "benchmark_regression",
        )
        cfg = SelfImprovementConfig(
            enabled=True,
            rules=RuleConfig(disabled_rules=all_names),
        )
        engine = build_rule_engine(cfg)
        assert engine.rule_count == 0


class TestBuildStrategies:
    """Strategy factory tests."""

    def test_config_tuning_only(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            config_tuning_enabled=True,
            architecture_proposals_enabled=False,
            prompt_tuning_enabled=False,
        )
        strategies = build_strategies(cfg)
        assert len(strategies) == 1
        assert strategies[0].altitude == ProposalAltitude.CONFIG_TUNING

    def test_all_deployment_strategies_enabled(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            config_tuning_enabled=True,
            architecture_proposals_enabled=True,
            prompt_tuning_enabled=True,
        )
        strategies = build_strategies(cfg)
        assert len(strategies) == 3
        altitudes = {s.altitude for s in strategies}
        assert altitudes == {
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.ARCHITECTURE,
            ProposalAltitude.PROMPT_TUNING,
        }

    def test_code_modification_without_provider_skipped(self) -> None:
        from synthorg.meta.config import CodeModificationConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=True,
            code_modification=CodeModificationConfig(
                github_token="test-token",
                github_repo="test/repo",
            ),
        )
        strategies = build_strategies(cfg, provider=None)
        altitudes = {s.altitude for s in strategies}
        assert ProposalAltitude.CODE_MODIFICATION not in altitudes

    def test_code_modification_with_provider_included(self) -> None:
        from unittest.mock import AsyncMock

        from synthorg.meta.config import CodeModificationConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=True,
            code_modification=CodeModificationConfig(
                github_token="test-token",
                github_repo="test/repo",
            ),
        )
        provider = AsyncMock()
        strategies = build_strategies(cfg, provider=provider)
        altitudes = {s.altitude for s in strategies}
        assert ProposalAltitude.CODE_MODIFICATION in altitudes

    def test_none_enabled(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            config_tuning_enabled=False,
            architecture_proposals_enabled=False,
            prompt_tuning_enabled=False,
        )
        strategies = build_strategies(cfg)
        assert len(strategies) == 0


class TestBuildGuards:
    """Guard factory tests."""

    def test_builds_4_guards(self) -> None:
        cfg = SelfImprovementConfig(enabled=True)
        guards = build_guards(cfg)
        assert len(guards) == 4

    def test_guard_chain_order(self) -> None:
        cfg = SelfImprovementConfig(enabled=True)
        guards = build_guards(cfg)
        names = [g.name for g in guards]
        assert names == [
            "scope_check",
            "rollback_plan",
            "rate_limit",
            "approval_gate",
        ]


class TestBuildAppliers:
    """Applier factory tests."""

    def test_builds_3_appliers_without_config(self) -> None:
        appliers = build_appliers()
        assert len(appliers) == 3
        assert ProposalAltitude.CONFIG_TUNING in appliers
        assert ProposalAltitude.ARCHITECTURE in appliers
        assert ProposalAltitude.PROMPT_TUNING in appliers

    def test_builds_3_appliers_with_code_mod_disabled(self) -> None:
        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=False,
        )
        appliers = build_appliers(cfg)
        assert len(appliers) == 3
        assert ProposalAltitude.CODE_MODIFICATION not in appliers

    def test_code_mod_enabled_without_creds_rejects(self) -> None:
        with pytest.raises(
            ValidationError,
            match="code_modification_enabled requires",
        ):
            SelfImprovementConfig(
                enabled=True,
                code_modification_enabled=True,
            )

    def test_builds_4_appliers_with_code_mod_and_creds(self) -> None:
        from pathlib import Path

        from synthorg.meta.config import CodeModificationConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=True,
            code_modification=CodeModificationConfig(
                github_token="test-token",
                github_repo="test/repo",
                project_root=str(Path.cwd()),
            ),
        )
        appliers = build_appliers(cfg)
        assert len(appliers) == 4
        assert ProposalAltitude.CODE_MODIFICATION in appliers

    def test_skips_code_applier_when_project_root_unset(self) -> None:
        """Fail closed: no absolute checkout means no code applier."""
        from synthorg.meta.config import CodeModificationConfig

        cfg = SelfImprovementConfig(
            enabled=True,
            code_modification_enabled=True,
            code_modification=CodeModificationConfig(
                github_token="test-token",
                github_repo="test/repo",
            ),
        )
        appliers = build_appliers(cfg)
        assert len(appliers) == 3
        assert ProposalAltitude.CODE_MODIFICATION not in appliers


class TestBuildRegressionDetector:
    """Regression detector factory tests."""

    def test_builds_tiered_detector(self) -> None:
        detector = build_regression_detector()
        assert detector.name == "tiered"


class TestBuildConfidenceAdjuster:
    """Confidence adjuster factory tests."""

    def test_default_builds_ema(self) -> None:
        config = SelfImprovementConfig()
        adjuster = build_confidence_adjuster(config)
        assert isinstance(adjuster, ExponentialMovingAverageAdjuster)
        assert adjuster.name == "ema"

    def test_ema_passes_alpha(self) -> None:
        from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig

        cos_cfg = ChiefOfStaffConfig(adjuster_strategy="ema", ema_alpha=0.3)
        config = SelfImprovementConfig(chief_of_staff=cos_cfg)
        adjuster = build_confidence_adjuster(config)
        assert isinstance(adjuster, ExponentialMovingAverageAdjuster)
        assert adjuster._alpha == pytest.approx(0.3)

    def test_bayesian_strategy(self) -> None:
        from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig

        cos_cfg = ChiefOfStaffConfig(adjuster_strategy="bayesian")
        config = SelfImprovementConfig(chief_of_staff=cos_cfg)
        adjuster = build_confidence_adjuster(config)
        assert isinstance(adjuster, BayesianConfidenceAdjuster)
        assert adjuster.name == "bayesian"


class TestBuildRolloutStrategies:
    """Rollout strategy factory tests."""

    def test_builds_3_strategies(self) -> None:
        strategies = build_rollout_strategies()
        assert set(strategies.keys()) == {
            "before_after",
            "canary",
            "ab_test",
        }
