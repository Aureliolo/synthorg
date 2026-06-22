"""Tests for QuotaPollerConfig and QuotaAlertThresholds."""

import pytest
from pydantic import ValidationError

from synthorg.budget.quota_poller_config import QuotaAlertThresholds, QuotaPollerConfig


@pytest.mark.unit
class TestQuotaAlertThresholds:
    """QuotaAlertThresholds validation."""

    def test_defaults(self) -> None:
        t = QuotaAlertThresholds()
        assert t.warn_pct == 80.0
        assert t.critical_pct == 95.0

    def test_custom_values(self) -> None:
        t = QuotaAlertThresholds(warn_pct=70.0, critical_pct=90.0)
        assert t.warn_pct == 70.0
        assert t.critical_pct == 90.0

    def test_warn_must_be_less_than_critical(self) -> None:
        with pytest.raises(ValidationError):
            QuotaAlertThresholds(warn_pct=90.0, critical_pct=80.0)

    def test_warn_equal_critical_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuotaAlertThresholds(warn_pct=80.0, critical_pct=80.0)

    def test_negative_warn_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuotaAlertThresholds(warn_pct=-1.0, critical_pct=95.0)

    def test_over_100_critical_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuotaAlertThresholds(warn_pct=80.0, critical_pct=101.0)

    def test_zero_warn_accepted(self) -> None:
        t = QuotaAlertThresholds(warn_pct=0.0, critical_pct=50.0)
        assert t.warn_pct == 0.0

    def test_hundred_critical_accepted(self) -> None:
        t = QuotaAlertThresholds(warn_pct=80.0, critical_pct=100.0)
        assert t.critical_pct == 100.0

    def test_frozen(self) -> None:
        t = QuotaAlertThresholds()
        with pytest.raises(ValidationError):
            t.warn_pct = 50.0  # type: ignore[misc]


@pytest.mark.unit
class TestQuotaPollerConfig:
    """QuotaPollerConfig validation and defaults."""

    def test_defaults(self) -> None:
        cfg = QuotaPollerConfig()
        # Wired on by default (pre-alpha posture); operators opt out via
        # the ``budget.quota_poller_enabled`` setting.
        assert cfg.enabled is True
        assert cfg.poll_interval_seconds == 60.0
        assert cfg.cooldown_seconds == 300.0
        assert isinstance(cfg.alert_thresholds, QuotaAlertThresholds)

    def test_enabled(self) -> None:
        cfg = QuotaPollerConfig(enabled=True)
        assert cfg.enabled is True

    def test_custom_interval(self) -> None:
        cfg = QuotaPollerConfig(poll_interval_seconds=120.0)
        assert cfg.poll_interval_seconds == 120.0

    def test_zero_interval_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuotaPollerConfig(poll_interval_seconds=0.0)

    def test_over_3600_interval_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuotaPollerConfig(poll_interval_seconds=3601.0)

    def test_custom_cooldown(self) -> None:
        cfg = QuotaPollerConfig(cooldown_seconds=60.0)
        assert cfg.cooldown_seconds == 60.0

    def test_custom_thresholds(self) -> None:
        cfg = QuotaPollerConfig(
            alert_thresholds=QuotaAlertThresholds(warn_pct=70.0, critical_pct=90.0)
        )
        assert cfg.alert_thresholds.warn_pct == 70.0

    def test_frozen(self) -> None:
        cfg = QuotaPollerConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = True  # type: ignore[misc]
