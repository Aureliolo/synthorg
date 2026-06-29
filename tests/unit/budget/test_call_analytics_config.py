"""Tests for CallAnalyticsConfig, RetryAlertConfig, and PromptClassAlertConfig."""

import math

import pytest
from pydantic import ValidationError

from synthorg.budget.call_analytics_config import (
    CallAnalyticsConfig,
    PromptClassAlertConfig,
    RetryAlertConfig,
)
from synthorg.budget.coordination_config import OrchestrationAlertThresholds


@pytest.mark.unit
class TestPromptClassAlertConfig:
    """PromptClassAlertConfig validation and defaults."""

    def test_defaults_disable_both_dimensions(self) -> None:
        cfg = PromptClassAlertConfig()
        assert cfg.cost_warn is None
        assert cfg.p95_latency_warn_ms is None
        assert cfg.min_seconds_between_alerts == 300.0

    def test_positive_thresholds_accepted(self) -> None:
        cfg = PromptClassAlertConfig(cost_warn=1.5, p95_latency_warn_ms=250.0)
        assert cfg.cost_warn == 1.5
        assert cfg.p95_latency_warn_ms == 250.0

    @pytest.mark.parametrize("field", ["cost_warn", "p95_latency_warn_ms"])
    def test_zero_threshold_rejected(self, field: str) -> None:
        # None is the disable switch, so 0.0 (which alerts on any value) is not
        # a meaningful ceiling and is rejected by gt=0.0.
        with pytest.raises(ValidationError):
            PromptClassAlertConfig(**{field: 0.0})

    @pytest.mark.parametrize("field", ["cost_warn", "p95_latency_warn_ms"])
    def test_negative_threshold_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            PromptClassAlertConfig(**{field: -1.0})

    @pytest.mark.parametrize("field", ["cost_warn", "p95_latency_warn_ms"])
    def test_non_finite_threshold_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            PromptClassAlertConfig(**{field: math.inf})

    def test_zero_window_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromptClassAlertConfig(min_seconds_between_alerts=0.0)

    def test_frozen(self) -> None:
        cfg = PromptClassAlertConfig()
        with pytest.raises(ValidationError):
            cfg.cost_warn = 1.0  # type: ignore[misc]


@pytest.mark.unit
class TestRetryAlertConfig:
    """RetryAlertConfig validation."""

    def test_defaults(self) -> None:
        cfg = RetryAlertConfig()
        assert cfg.warn_rate == 0.10

    def test_custom_warn_rate(self) -> None:
        cfg = RetryAlertConfig(warn_rate=0.25)
        assert cfg.warn_rate == 0.25

    def test_zero_accepted(self) -> None:
        cfg = RetryAlertConfig(warn_rate=0.0)
        assert cfg.warn_rate == 0.0

    def test_one_accepted(self) -> None:
        cfg = RetryAlertConfig(warn_rate=1.0)
        assert cfg.warn_rate == 1.0

    def test_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RetryAlertConfig(warn_rate=1.1)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RetryAlertConfig(warn_rate=-0.01)

    def test_frozen(self) -> None:
        cfg = RetryAlertConfig()
        with pytest.raises(ValidationError):
            cfg.warn_rate = 0.5  # type: ignore[misc]


@pytest.mark.unit
class TestCallAnalyticsConfig:
    """CallAnalyticsConfig validation and defaults."""

    def test_defaults(self) -> None:
        cfg = CallAnalyticsConfig()
        assert cfg.enabled is True
        assert isinstance(cfg.orchestration_alerts, OrchestrationAlertThresholds)
        assert isinstance(cfg.retry_alerts, RetryAlertConfig)
        assert isinstance(cfg.prompt_class_alerts, PromptClassAlertConfig)
        assert cfg.prompt_class_alerts.cost_warn is None
        assert cfg.prompt_class_alerts.p95_latency_warn_ms is None

    def test_disabled(self) -> None:
        cfg = CallAnalyticsConfig(enabled=False)
        assert cfg.enabled is False

    def test_custom_retry_alerts(self) -> None:
        cfg = CallAnalyticsConfig(retry_alerts=RetryAlertConfig(warn_rate=0.20))
        assert cfg.retry_alerts.warn_rate == 0.20

    def test_custom_orchestration_alerts(self) -> None:
        cfg = CallAnalyticsConfig(
            orchestration_alerts=OrchestrationAlertThresholds(
                info=0.10, warn=0.40, critical=0.60
            )
        )
        assert cfg.orchestration_alerts.warn == 0.40

    def test_frozen(self) -> None:
        cfg = CallAnalyticsConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]
