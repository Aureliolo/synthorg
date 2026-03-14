"""Tests for budget configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.budget.config import (
    AutoDowngradeConfig,
    BudgetAlertConfig,
    BudgetConfig,
)

from .conftest import (
    AutoDowngradeConfigFactory,
    BudgetAlertConfigFactory,
    BudgetConfigFactory,
)

pytestmark = pytest.mark.timeout(30)

# ── BudgetAlertConfig ─────────────────────────────────────────────


@pytest.mark.unit
class TestBudgetAlertConfig:
    """Tests for BudgetAlertConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        """Verify default threshold values."""
        cfg = BudgetAlertConfig()
        assert cfg.warn_at == 75
        assert cfg.critical_at == 90
        assert cfg.hard_stop_at == 100

    def test_custom_values(self) -> None:
        """Accept valid custom thresholds."""
        cfg = BudgetAlertConfig(warn_at=50, critical_at=70, hard_stop_at=90)
        assert cfg.warn_at == 50
        assert cfg.critical_at == 70
        assert cfg.hard_stop_at == 90

    def test_warn_at_boundary_zero(self) -> None:
        """Accept warn_at at lower boundary (0)."""
        cfg = BudgetAlertConfig(warn_at=0, critical_at=50, hard_stop_at=100)
        assert cfg.warn_at == 0

    def test_hard_stop_at_boundary_100(self) -> None:
        """Accept hard_stop_at at upper boundary (100)."""
        cfg = BudgetAlertConfig(warn_at=10, critical_at=50, hard_stop_at=100)
        assert cfg.hard_stop_at == 100

    def test_float_threshold_rejected(self) -> None:
        """Reject float value for threshold (strict int)."""
        with pytest.raises(ValidationError):
            BudgetAlertConfig(warn_at=75.5, critical_at=90, hard_stop_at=100)  # type: ignore[arg-type]

    def test_negative_threshold_rejected(self) -> None:
        """Reject negative threshold values."""
        with pytest.raises(ValidationError):
            BudgetAlertConfig(warn_at=-1, critical_at=90, hard_stop_at=100)

    def test_threshold_over_100_rejected(self) -> None:
        """Reject threshold values above 100."""
        with pytest.raises(ValidationError):
            BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=101)

    def test_warn_equals_critical_rejected(self) -> None:
        """Reject warn_at equal to critical_at."""
        with pytest.raises(ValidationError, match="Alert thresholds must be ordered"):
            BudgetAlertConfig(warn_at=90, critical_at=90, hard_stop_at=100)

    def test_critical_equals_hard_stop_rejected(self) -> None:
        """Reject critical_at equal to hard_stop_at."""
        with pytest.raises(ValidationError, match="Alert thresholds must be ordered"):
            BudgetAlertConfig(warn_at=75, critical_at=100, hard_stop_at=100)

    def test_thresholds_reversed_rejected(self) -> None:
        """Reject thresholds in wrong order."""
        with pytest.raises(ValidationError, match="Alert thresholds must be ordered"):
            BudgetAlertConfig(warn_at=90, critical_at=80, hard_stop_at=70)

    def test_frozen(self) -> None:
        """Ensure BudgetAlertConfig is immutable."""
        cfg = BudgetAlertConfig()
        with pytest.raises(ValidationError):
            cfg.warn_at = 50  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        cfg = BudgetAlertConfigFactory.build()
        assert isinstance(cfg, BudgetAlertConfig)


# ── AutoDowngradeConfig ───────────────────────────────────────────


@pytest.mark.unit
class TestAutoDowngradeConfig:
    """Tests for AutoDowngradeConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        """Verify default values."""
        cfg = AutoDowngradeConfig()
        assert cfg.enabled is False
        assert cfg.threshold == 85
        assert cfg.downgrade_map == ()
        assert cfg.boundary == "task_assignment"

    def test_custom_values(self) -> None:
        """Accept valid custom configuration."""
        cfg = AutoDowngradeConfig(
            enabled=True,
            threshold=80,
            downgrade_map=(("large", "medium"), ("medium", "small")),
        )
        assert cfg.enabled is True
        assert cfg.threshold == 80
        assert len(cfg.downgrade_map) == 2

    def test_empty_source_alias_rejected(self) -> None:
        """Reject whitespace-only source alias in downgrade_map."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            AutoDowngradeConfig(
                enabled=True,
                downgrade_map=(("  ", "medium"),),
            )

    def test_empty_target_alias_rejected(self) -> None:
        """Reject whitespace-only target alias in downgrade_map."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            AutoDowngradeConfig(
                enabled=True,
                downgrade_map=(("large", "  "),),
            )

    def test_self_downgrade_rejected(self) -> None:
        """Reject downgrade_map entry where source equals target."""
        with pytest.raises(ValidationError, match="Self-downgrade"):
            AutoDowngradeConfig(
                enabled=True,
                downgrade_map=(("large", "large"),),
            )

    def test_duplicate_source_alias_rejected(self) -> None:
        """Reject duplicate source aliases in downgrade_map."""
        with pytest.raises(ValidationError, match="Duplicate source aliases"):
            AutoDowngradeConfig(
                enabled=True,
                downgrade_map=(
                    ("large", "medium"),
                    ("large", "small"),
                ),
            )

    def test_float_threshold_rejected(self) -> None:
        """Reject float value for threshold (strict int)."""
        with pytest.raises(ValidationError):
            AutoDowngradeConfig(threshold=85.5)  # type: ignore[arg-type]

    def test_threshold_boundary_0(self) -> None:
        """Accept threshold at lower boundary (0)."""
        cfg = AutoDowngradeConfig(threshold=0)
        assert cfg.threshold == 0

    def test_threshold_boundary_100(self) -> None:
        """Accept threshold at upper boundary (100)."""
        cfg = AutoDowngradeConfig(threshold=100)
        assert cfg.threshold == 100

    def test_threshold_negative_rejected(self) -> None:
        """Reject negative threshold."""
        with pytest.raises(ValidationError):
            AutoDowngradeConfig(threshold=-1)

    def test_threshold_over_100_rejected(self) -> None:
        """Reject threshold above 100."""
        with pytest.raises(ValidationError):
            AutoDowngradeConfig(threshold=101)

    def test_aliases_normalized(self) -> None:
        """Verify whitespace is stripped from aliases."""
        cfg = AutoDowngradeConfig(
            enabled=True,
            downgrade_map=(("  large  ", "  medium  "),),
        )
        assert cfg.downgrade_map == (("large", "medium"),)

    def test_boundary_default_is_task_assignment(self) -> None:
        """Verify boundary default is 'task_assignment'."""
        cfg = AutoDowngradeConfig()
        assert cfg.boundary == "task_assignment"

    def test_boundary_rejects_other_values(self) -> None:
        """Reject boundary values other than 'task_assignment'."""
        with pytest.raises(ValidationError):
            AutoDowngradeConfig(boundary="mid_execution")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        """Ensure AutoDowngradeConfig is immutable."""
        cfg = AutoDowngradeConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = True  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        cfg = AutoDowngradeConfigFactory.build()
        assert isinstance(cfg, AutoDowngradeConfig)


# ── BudgetConfig ──────────────────────────────────────────────────


@pytest.mark.unit
class TestBudgetConfig:
    """Tests for BudgetConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        """Verify all default values including nested defaults."""
        cfg = BudgetConfig()
        assert cfg.total_monthly == 100.0
        assert cfg.per_task_limit == 5.0
        assert cfg.per_agent_daily_limit == 10.0
        assert cfg.alerts.warn_at == 75
        assert cfg.auto_downgrade.enabled is False
        assert cfg.reset_day == 1

    def test_custom_values(self, sample_budget_config: BudgetConfig) -> None:
        """Accept valid custom budget config."""
        assert sample_budget_config.total_monthly == 500.0
        assert sample_budget_config.per_task_limit == 10.0
        assert sample_budget_config.per_agent_daily_limit == 25.0
        assert sample_budget_config.auto_downgrade.enabled is True

    def test_zero_monthly_budget_accepted(self) -> None:
        """Accept zero monthly budget."""
        cfg = BudgetConfig(total_monthly=0.0)
        assert cfg.total_monthly == 0.0

    def test_negative_monthly_budget_rejected(self) -> None:
        """Reject negative monthly budget."""
        with pytest.raises(ValidationError):
            BudgetConfig(total_monthly=-1.0)

    def test_per_task_exceeds_monthly_rejected(self) -> None:
        """Reject per_task_limit exceeding total_monthly."""
        with pytest.raises(ValidationError, match="per_task_limit"):
            BudgetConfig(total_monthly=10.0, per_task_limit=20.0)

    def test_per_agent_exceeds_monthly_rejected(self) -> None:
        """Reject per_agent_daily_limit exceeding total_monthly."""
        with pytest.raises(ValidationError, match="per_agent_daily_limit"):
            BudgetConfig(total_monthly=10.0, per_agent_daily_limit=20.0)

    def test_per_task_equals_monthly_accepted(self) -> None:
        """Accept per_task_limit equal to total_monthly."""
        cfg = BudgetConfig(total_monthly=10.0, per_task_limit=10.0)
        assert cfg.per_task_limit == cfg.total_monthly

    def test_per_agent_equals_monthly_accepted(self) -> None:
        """Accept per_agent_daily_limit equal to total_monthly."""
        cfg = BudgetConfig(total_monthly=10.0, per_agent_daily_limit=10.0)
        assert cfg.per_agent_daily_limit == cfg.total_monthly

    def test_zero_monthly_skips_limit_validation(self) -> None:
        """When total_monthly is 0, skip limit checks."""
        cfg = BudgetConfig(
            total_monthly=0.0,
            per_task_limit=100.0,
            per_agent_daily_limit=100.0,
        )
        assert cfg.per_task_limit == 100.0
        assert cfg.per_agent_daily_limit == 100.0

    def test_reset_day_valid_range(self) -> None:
        """Accept reset_day in valid range (1-28)."""
        cfg_1 = BudgetConfig(reset_day=1)
        assert cfg_1.reset_day == 1
        cfg_28 = BudgetConfig(reset_day=28)
        assert cfg_28.reset_day == 28

    def test_reset_day_zero_rejected(self) -> None:
        """Reject reset_day of 0."""
        with pytest.raises(ValidationError):
            BudgetConfig(reset_day=0)

    def test_reset_day_29_rejected(self) -> None:
        """Reject reset_day of 29 (avoids month-length issues)."""
        with pytest.raises(ValidationError):
            BudgetConfig(reset_day=29)

    def test_reset_day_float_rejected(self) -> None:
        """Reject float value for reset_day (strict int)."""
        with pytest.raises(ValidationError):
            BudgetConfig(reset_day=15.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "value",
        [float("inf"), float("-inf"), float("nan")],
        ids=["inf", "neg_inf", "nan"],
    )
    def test_inf_nan_rejected(self, value: float) -> None:
        """Reject inf and NaN values for float fields."""
        with pytest.raises(ValidationError):
            BudgetConfig(total_monthly=value)

    def test_frozen(self) -> None:
        """Ensure BudgetConfig is immutable."""
        cfg = BudgetConfig()
        with pytest.raises(ValidationError):
            cfg.total_monthly = 200.0  # type: ignore[misc]

    def test_json_roundtrip(self, sample_budget_config: BudgetConfig) -> None:
        """Verify JSON serialization and deserialization preserves fields."""
        json_str = sample_budget_config.model_dump_json()
        restored = BudgetConfig.model_validate_json(json_str)
        assert restored.total_monthly == sample_budget_config.total_monthly
        assert restored.alerts.warn_at == sample_budget_config.alerts.warn_at
        assert (
            restored.auto_downgrade.enabled
            == sample_budget_config.auto_downgrade.enabled
        )

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        cfg = BudgetConfigFactory.build()
        assert isinstance(cfg, BudgetConfig)
