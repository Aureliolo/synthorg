"""Tests for SandboxLifecycleConfig model."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.lifecycle.config import (
    LifecycleStrategy,
    SandboxLifecycleConfig,
)

pytestmark = pytest.mark.unit


class TestSandboxLifecycleConfigDefaults:
    """Default values match design spec."""

    def test_defaults(self) -> None:
        config = SandboxLifecycleConfig()
        assert config.strategy is LifecycleStrategy.PER_AGENT
        assert config.grace_period_seconds == 30.0
        assert config.health_check_interval_seconds == 10.0
        assert config.max_idle_seconds == 300.0

    def test_frozen(self) -> None:
        config = SandboxLifecycleConfig()
        with pytest.raises(ValidationError):
            config.strategy = LifecycleStrategy.PER_CALL  # type: ignore[misc]


class TestSandboxLifecycleConfigValidation:
    """Field validation rules."""

    @pytest.mark.parametrize("strategy", ["per-agent", "per-task", "per-call"])
    def test_the_wire_form_is_read_into_the_enum(self, strategy: str) -> None:
        config = SandboxLifecycleConfig(strategy=strategy)  # type: ignore[arg-type]
        assert config.strategy is LifecycleStrategy(strategy)

    def test_invalid_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy"):
            SandboxLifecycleConfig(strategy="per-request")  # type: ignore[arg-type]

    def test_negative_grace_period_rejected(self) -> None:
        with pytest.raises(ValidationError, match="grace_period_seconds"):
            SandboxLifecycleConfig(grace_period_seconds=-1.0)

    def test_zero_grace_period_allowed(self) -> None:
        config = SandboxLifecycleConfig(grace_period_seconds=0.0)
        assert config.grace_period_seconds == 0.0

    def test_health_check_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError, match="health_check_interval_seconds"):
            SandboxLifecycleConfig(health_check_interval_seconds=0.5)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SandboxLifecycleConfig(grace_period_seconds=float("nan"))

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SandboxLifecycleConfig(grace_period_seconds=float("inf"))
