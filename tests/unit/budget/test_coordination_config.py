"""Tests for coordination metrics configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.budget.coordination_config import (
    CoordinationMetricName,
    CoordinationMetricsConfig,
    ErrorCategory,
    ErrorTaxonomyConfig,
    OrchestrationAlertThresholds,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCoordinationMetricName:
    """CoordinationMetricName enum."""

    def test_values(self) -> None:
        assert CoordinationMetricName.EFFICIENCY.value == "efficiency"
        assert CoordinationMetricName.OVERHEAD.value == "overhead"
        assert CoordinationMetricName.ERROR_AMPLIFICATION.value == "error_amplification"
        assert CoordinationMetricName.MESSAGE_DENSITY.value == "message_density"
        assert CoordinationMetricName.REDUNDANCY.value == "redundancy"

    def test_member_count(self) -> None:
        assert len(CoordinationMetricName) == 5


@pytest.mark.unit
class TestErrorCategory:
    """ErrorCategory enum."""

    def test_values(self) -> None:
        assert ErrorCategory.LOGICAL_CONTRADICTION.value == "logical_contradiction"
        assert ErrorCategory.NUMERICAL_DRIFT.value == "numerical_drift"
        assert ErrorCategory.CONTEXT_OMISSION.value == "context_omission"
        assert ErrorCategory.COORDINATION_FAILURE.value == "coordination_failure"

    def test_member_count(self) -> None:
        assert len(ErrorCategory) == 4


@pytest.mark.unit
class TestErrorTaxonomyConfig:
    """ErrorTaxonomyConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = ErrorTaxonomyConfig()
        assert config.enabled is False
        assert len(config.categories) == 4

    def test_custom(self) -> None:
        config = ErrorTaxonomyConfig(
            enabled=True,
            categories=(
                ErrorCategory.LOGICAL_CONTRADICTION,
                ErrorCategory.NUMERICAL_DRIFT,
            ),
        )
        assert config.enabled is True
        assert len(config.categories) == 2


@pytest.mark.unit
class TestOrchestrationAlertThresholds:
    """OrchestrationAlertThresholds validation."""

    def test_defaults(self) -> None:
        t = OrchestrationAlertThresholds()
        assert t.info == 0.30
        assert t.warn == 0.50
        assert t.critical == 0.70

    def test_custom_valid(self) -> None:
        t = OrchestrationAlertThresholds(
            info=0.10,
            warn=0.20,
            critical=0.30,
        )
        assert t.info == 0.10
        assert t.warn == 0.20
        assert t.critical == 0.30

    def test_non_ordered_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly ordered"):
            OrchestrationAlertThresholds(
                info=0.50,
                warn=0.30,
                critical=0.70,
            )

    def test_equal_thresholds_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly ordered"):
            OrchestrationAlertThresholds(
                info=0.30,
                warn=0.30,
                critical=0.70,
            )

    def test_info_equals_critical_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly ordered"):
            OrchestrationAlertThresholds(
                info=0.50,
                warn=0.60,
                critical=0.50,
            )

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrchestrationAlertThresholds(
                info=-0.1,
                warn=0.50,
                critical=0.70,
            )

    def test_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrchestrationAlertThresholds(
                info=0.30,
                warn=0.50,
                critical=1.1,
            )

    def test_frozen(self) -> None:
        t = OrchestrationAlertThresholds()
        with pytest.raises(ValidationError):
            t.info = 0.1  # type: ignore[misc]


@pytest.mark.unit
class TestCoordinationMetricsConfig:
    """CoordinationMetricsConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = CoordinationMetricsConfig()
        assert config.enabled is False
        assert len(config.collect) == 5
        assert config.baseline_window == 50
        assert config.error_taxonomy.enabled is False
        assert config.orchestration_alerts.info == 0.30

    def test_enabled_with_subset(self) -> None:
        config = CoordinationMetricsConfig(
            enabled=True,
            collect=(
                CoordinationMetricName.EFFICIENCY,
                CoordinationMetricName.OVERHEAD,
            ),
        )
        assert config.enabled is True
        assert len(config.collect) == 2

    def test_custom_baseline_window(self) -> None:
        config = CoordinationMetricsConfig(baseline_window=100)
        assert config.baseline_window == 100

    def test_zero_baseline_window_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoordinationMetricsConfig(baseline_window=0)

    def test_negative_baseline_window_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoordinationMetricsConfig(baseline_window=-1)

    def test_frozen(self) -> None:
        config = CoordinationMetricsConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # type: ignore[misc]
