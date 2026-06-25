"""Tests for risk budget configuration models."""

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.risk_config import RiskBudgetAlertConfig, RiskBudgetConfig


@pytest.mark.unit
class TestRiskBudgetAlertConfig:
    """Tests for RiskBudgetAlertConfig."""

    def test_defaults(self) -> None:
        cfg = RiskBudgetAlertConfig()
        assert cfg.warn_at == 75
        assert cfg.critical_at == 90

    def test_custom_values(self) -> None:
        cfg = RiskBudgetAlertConfig(warn_at=50, critical_at=80)
        assert cfg.warn_at == 50
        assert cfg.critical_at == 80

    def test_warn_must_be_less_than_critical(self) -> None:
        with pytest.raises(ValueError, match=r"warn_at.*critical_at"):
            RiskBudgetAlertConfig(warn_at=90, critical_at=75)

    def test_equal_warn_critical_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"warn_at.*critical_at"):
            RiskBudgetAlertConfig(warn_at=80, critical_at=80)

    def test_frozen(self) -> None:
        cfg = RiskBudgetAlertConfig()
        with pytest.raises(Exception):  # noqa: B017, PT011
            cfg.warn_at = 50  # type: ignore[misc]

    def test_bounds_lower(self) -> None:
        with pytest.raises(ValueError, match=r"greater than or equal"):
            RiskBudgetAlertConfig(warn_at=-1, critical_at=90)

    def test_bounds_upper(self) -> None:
        with pytest.raises(ValueError, match=r"less than or equal"):
            RiskBudgetAlertConfig(warn_at=75, critical_at=101)


@pytest.mark.unit
class TestRiskBudgetConfig:
    """Tests for RiskBudgetConfig."""

    def test_defaults(self) -> None:
        cfg = RiskBudgetConfig()
        # On by default (the budget.risk_enabled setting ships "true").
        assert cfg.enabled is True
        assert cfg.per_task_risk_limit == 5.0
        assert cfg.per_agent_daily_risk_limit == 20.0
        assert cfg.total_daily_risk_limit == 100.0
        assert isinstance(cfg.alerts, RiskBudgetAlertConfig)

    def test_enabled(self) -> None:
        cfg = RiskBudgetConfig(enabled=True)
        assert cfg.enabled is True

    def test_frozen(self) -> None:
        cfg = RiskBudgetConfig()
        with pytest.raises(Exception):  # noqa: B017, PT011
            cfg.enabled = True  # type: ignore[misc]

    def test_negative_limits_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            RiskBudgetConfig(per_task_risk_limit=-1.0)

    def test_task_limit_exceeds_total_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"per_task_risk_limit.*total_daily_risk_limit",
        ):
            RiskBudgetConfig(
                per_task_risk_limit=200.0,
                per_agent_daily_risk_limit=200.0,
                total_daily_risk_limit=100.0,
            )

    def test_agent_daily_limit_exceeds_total_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"per_agent_daily_risk_limit.*total_daily_risk_limit",
        ):
            RiskBudgetConfig(
                per_agent_daily_risk_limit=200.0,
                total_daily_risk_limit=100.0,
            )

    def test_task_limit_exceeds_agent_daily_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"per_task_risk_limit.*per_agent_daily_risk_limit",
        ):
            RiskBudgetConfig(
                per_task_risk_limit=15.0,
                per_agent_daily_risk_limit=10.0,
                total_daily_risk_limit=100.0,
            )

    def test_task_limit_equals_total_allowed(self) -> None:
        cfg = RiskBudgetConfig(
            per_task_risk_limit=100.0,
            per_agent_daily_risk_limit=100.0,
            total_daily_risk_limit=100.0,
        )
        assert cfg.per_task_risk_limit == 100.0

    def test_zero_total_skips_task_limit_check(self) -> None:
        cfg = RiskBudgetConfig(
            per_task_risk_limit=5.0,
            total_daily_risk_limit=0.0,
        )
        assert cfg.total_daily_risk_limit == 0.0

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match=r"finite"):
            RiskBudgetConfig(per_task_risk_limit=float("nan"))


@pytest.mark.unit
class TestBudgetConfigRiskIntegration:
    """Tests for risk_budget field on BudgetConfig."""

    def test_default_risk_budget(self) -> None:
        cfg = BudgetConfig()
        assert isinstance(cfg.risk_budget, RiskBudgetConfig)
        assert cfg.risk_budget.enabled is True

    def test_custom_risk_budget(self) -> None:
        risk_cfg = RiskBudgetConfig(enabled=True, per_task_risk_limit=10.0)
        cfg = BudgetConfig(risk_budget=risk_cfg)
        assert cfg.risk_budget.enabled is True
        assert cfg.risk_budget.per_task_risk_limit == 10.0
