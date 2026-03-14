"""Tests for trust configuration models."""

from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ToolAccessLevel
from synthorg.security.trust.config import (
    CategoryTrustCriteria,
    MilestoneCriteria,
    ReVerificationConfig,
    TrustConfig,
    TrustThreshold,
    WeightedTrustWeights,
)
from synthorg.security.trust.enums import TrustStrategyType

pytestmark = pytest.mark.timeout(30)


# ── TrustConfig Defaults ────────────────────────────────────────


@pytest.mark.unit
class TestTrustConfigDefaults:
    """Tests for TrustConfig default values."""

    def test_default_strategy_is_disabled(self) -> None:
        config = TrustConfig()
        assert config.strategy == TrustStrategyType.DISABLED

    def test_default_initial_level_is_standard(self) -> None:
        config = TrustConfig()
        assert config.initial_level == ToolAccessLevel.STANDARD

    def test_default_weights(self) -> None:
        config = TrustConfig()
        assert config.weights.task_difficulty == 0.3
        assert config.weights.completion_rate == 0.25
        assert config.weights.error_rate == 0.25
        assert config.weights.human_feedback == 0.2

    def test_default_empty_thresholds(self) -> None:
        config = TrustConfig()
        assert config.promotion_thresholds == {}

    def test_default_empty_category_levels(self) -> None:
        config = TrustConfig()
        assert config.initial_category_levels == {}

    def test_default_empty_milestones(self) -> None:
        config = TrustConfig()
        assert config.milestones == {}

    def test_frozen(self) -> None:
        config = TrustConfig()
        with pytest.raises(ValidationError):
            config.strategy = TrustStrategyType.WEIGHTED  # type: ignore[misc]


# ── WeightedTrustWeights ────────────────────────────────────────


@pytest.mark.unit
class TestWeightedTrustWeights:
    """Tests for WeightedTrustWeights sum validation."""

    def test_valid_weights_sum_to_one(self) -> None:
        weights = WeightedTrustWeights(
            task_difficulty=0.25,
            completion_rate=0.25,
            error_rate=0.25,
            human_feedback=0.25,
        )
        total = (
            weights.task_difficulty
            + weights.completion_rate
            + weights.error_rate
            + weights.human_feedback
        )
        assert abs(total - 1.0) < 0.01

    def test_default_weights_sum_to_one(self) -> None:
        weights = WeightedTrustWeights()
        total = (
            weights.task_difficulty
            + weights.completion_rate
            + weights.error_rate
            + weights.human_feedback
        )
        assert abs(total - 1.0) < 0.01

    def test_weights_within_tolerance(self) -> None:
        """Weights summing to 1.0 within 0.01 tolerance should pass."""
        weights = WeightedTrustWeights(
            task_difficulty=0.305,
            completion_rate=0.25,
            error_rate=0.25,
            human_feedback=0.2,
        )
        assert weights.task_difficulty == 0.305

    def test_weights_not_summing_to_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            WeightedTrustWeights(
                task_difficulty=0.5,
                completion_rate=0.5,
                error_rate=0.5,
                human_feedback=0.5,
            )

    def test_weights_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            WeightedTrustWeights(
                task_difficulty=0.1,
                completion_rate=0.1,
                error_rate=0.1,
                human_feedback=0.1,
            )

    def test_frozen(self) -> None:
        weights = WeightedTrustWeights()
        with pytest.raises(ValidationError):
            weights.task_difficulty = 0.5  # type: ignore[misc]


# ── Elevated Gate Invariant ──────────────────────────────────────


@pytest.mark.unit
class TestElevatedGateInvariant:
    """Tests for the security invariant: standard_to_elevated requires human."""

    def test_threshold_without_human_approval_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"standard_to_elevated.*requires_human_approval",
        ):
            TrustConfig(
                strategy=TrustStrategyType.WEIGHTED,
                promotion_thresholds={
                    "standard_to_elevated": TrustThreshold(
                        score=0.9,
                        requires_human_approval=False,
                    ),
                },
            )

    def test_threshold_with_human_approval_passes(self) -> None:
        config = TrustConfig(
            strategy=TrustStrategyType.WEIGHTED,
            promotion_thresholds={
                "standard_to_elevated": TrustThreshold(
                    score=0.9,
                    requires_human_approval=True,
                ),
            },
        )
        threshold = config.promotion_thresholds["standard_to_elevated"]
        assert threshold.requires_human_approval is True

    def test_milestone_without_human_approval_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"standard_to_elevated.*requires_human_approval",
        ):
            TrustConfig(
                strategy=TrustStrategyType.MILESTONE,
                milestones={
                    "standard_to_elevated": MilestoneCriteria(
                        tasks_completed=30,
                        requires_human_approval=False,
                    ),
                },
            )

    def test_milestone_with_human_approval_passes(self) -> None:
        config = TrustConfig(
            strategy=TrustStrategyType.MILESTONE,
            milestones={
                "standard_to_elevated": MilestoneCriteria(
                    tasks_completed=30,
                    auto_promote=False,
                    requires_human_approval=True,
                ),
            },
        )
        milestone = config.milestones["standard_to_elevated"]
        assert milestone.requires_human_approval is True

    def test_category_criteria_without_human_approval_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"standard_to_elevated.*requires_human_approval",
        ):
            TrustConfig(
                strategy=TrustStrategyType.PER_CATEGORY,
                initial_category_levels={
                    "file_system": ToolAccessLevel.STANDARD,
                },
                category_criteria={
                    "file_system": {
                        "standard_to_elevated": CategoryTrustCriteria(
                            requires_human_approval=False,
                        ),
                    },
                },
            )


# ── Per-Category Requires Initial Levels ─────────────────────────


@pytest.mark.unit
class TestPerCategoryValidation:
    """Tests for per_category strategy validation."""

    def test_per_category_without_initial_levels_raises(self) -> None:
        with pytest.raises(
            ValueError,
            match="initial_category_levels",
        ):
            TrustConfig(
                strategy=TrustStrategyType.PER_CATEGORY,
            )

    def test_per_category_with_initial_levels_passes(self) -> None:
        config = TrustConfig(
            strategy=TrustStrategyType.PER_CATEGORY,
            initial_category_levels={
                "file_system": ToolAccessLevel.SANDBOXED,
                "code_execution": ToolAccessLevel.RESTRICTED,
            },
        )
        assert len(config.initial_category_levels) == 2


# ── Parametrize Strategies ───────────────────────────────────────


@pytest.mark.unit
class TestStrategyParametrize:
    """Test that all strategy types can be configured."""

    @pytest.mark.parametrize(
        ("strategy", "extra_kwargs"),
        [
            (TrustStrategyType.DISABLED, {}),
            (
                TrustStrategyType.WEIGHTED,
                {
                    "promotion_thresholds": {
                        "sandboxed_to_restricted": TrustThreshold(
                            score=0.5,
                        ),
                    },
                },
            ),
            (
                TrustStrategyType.PER_CATEGORY,
                {
                    "initial_category_levels": {
                        "file_system": ToolAccessLevel.SANDBOXED,
                    },
                },
            ),
            (
                TrustStrategyType.MILESTONE,
                {
                    "milestones": {
                        "sandboxed_to_restricted": MilestoneCriteria(
                            tasks_completed=5,
                        ),
                    },
                },
            ),
        ],
        ids=["disabled", "weighted", "per_category", "milestone"],
    )
    def test_strategy_config_creation(
        self,
        strategy: TrustStrategyType,
        extra_kwargs: dict[str, Any],
    ) -> None:
        config = TrustConfig(strategy=strategy, **extra_kwargs)
        assert config.strategy == strategy


# ── MilestoneCriteria Mutual Exclusivity ─────────────────────────


@pytest.mark.unit
class TestMilestoneCriteriaApprovalFlags:
    """Tests for MilestoneCriteria._validate_approval_flags."""

    def test_auto_promote_and_requires_human_raises(self) -> None:
        """auto_promote=True with requires_human_approval=True raises."""
        with pytest.raises(ValueError, match="mutually exclusive"):
            MilestoneCriteria(
                auto_promote=True,
                requires_human_approval=True,
            )


# ── ReVerificationConfig Constraints ─────────────────────────────


@pytest.mark.unit
class TestReVerificationConfigConstraints:
    """Tests for ReVerificationConfig field constraints."""

    def test_interval_days_zero_raises(self) -> None:
        """interval_days=0 violates ge=1 constraint."""
        with pytest.raises(ValidationError):
            ReVerificationConfig(interval_days=0)

    def test_decay_on_error_rate_above_one_raises(self) -> None:
        """decay_on_error_rate=1.5 violates le=1.0 constraint."""
        with pytest.raises(ValidationError):
            ReVerificationConfig(decay_on_error_rate=1.5)
