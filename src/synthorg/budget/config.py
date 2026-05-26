"""Budget configuration models.

Implements the Cost Controls section of the Operations design page: alert
thresholds, per-task and per-agent limits, automatic model downgrade,
and risk budget configuration.
"""

from collections import Counter
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.risk_config import RiskBudgetConfig
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_float,
    parse_int,
)


class BudgetAlertConfig(BaseModel):
    """Alert threshold configuration for budget monitoring.

    Thresholds are expressed as percentages of the total monthly budget.
    They must be strictly ordered: ``warn_at < critical_at < hard_stop_at``.

    Attributes:
        warn_at: Percentage of budget that triggers a warning alert.
        critical_at: Percentage of budget that triggers a critical alert.
        hard_stop_at: Percentage of budget that triggers a hard stop.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="warn_at",
            namespace=SettingNamespace.BUDGET,
            key="alert_warn_at",
            parse=parse_int,
        ),
        MirrorField(
            field="critical_at",
            namespace=SettingNamespace.BUDGET,
            key="alert_critical_at",
            parse=parse_int,
        ),
        MirrorField(
            field="hard_stop_at",
            namespace=SettingNamespace.BUDGET,
            key="alert_hard_stop_at",
            parse=parse_int,
        ),
    )

    warn_at: int = Field(
        default=75,
        ge=0,
        le=100,
        strict=True,
        description="Percent of budget triggering warning",
    )
    critical_at: int = Field(
        default=90,
        ge=0,
        le=100,
        strict=True,
        description="Percent of budget triggering critical alert",
    )
    hard_stop_at: int = Field(
        default=100,
        ge=0,
        le=100,
        strict=True,
        description="Percent of budget triggering hard stop",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        """Apply mirrors.

        Returns:
            Result of type ``Any``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure thresholds are strictly ordered.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not (self.warn_at < self.critical_at < self.hard_stop_at):
            msg = (
                f"Alert thresholds must be ordered: "
                f"warn_at ({self.warn_at}) < "
                f"critical_at ({self.critical_at}) < "
                f"hard_stop_at ({self.hard_stop_at})"
            )
            raise ValueError(msg)
        return self


class AutoDowngradeConfig(BaseModel):
    """Automatic model downgrade configuration.

    When ``enabled``, models are downgraded to cheaper alternatives once
    budget usage exceeds ``threshold`` percent. The ``downgrade_map`` is
    stored as a tuple of ``(source_alias, target_alias)`` pairs to
    maintain immutability.

    Attributes:
        enabled: Whether auto-downgrade is active.
        threshold: Budget percent that triggers downgrade.
        downgrade_map: Ordered pairs of (from_alias, to_alias).
        boundary: When to apply downgrade (task_assignment only,
            never mid-execution per the Operations design page).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.BUDGET,
            key="auto_downgrade_enabled",
            parse=parse_bool,
        ),
        MirrorField(
            field="threshold",
            namespace=SettingNamespace.BUDGET,
            key="auto_downgrade_threshold",
            parse=parse_int,
        ),
    )

    enabled: bool = Field(
        default=False,
        description="Whether auto-downgrade is active",
    )
    threshold: int = Field(
        default=85,
        ge=0,
        le=100,
        strict=True,
        description="Budget percent triggering downgrade",
    )
    downgrade_map: tuple[tuple[NotBlankStr, NotBlankStr], ...] = Field(
        default=(),
        description="Ordered pairs of (from_alias, to_alias)",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        """Apply mirrors.

        Returns:
            Result of type ``Any``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    boundary: Literal["task_assignment"] = Field(
        default="task_assignment",
        description=(
            "When to apply downgrade (task_assignment only, never mid-execution)"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_downgrade_map(cls, data: Any) -> Any:
        """Normalize downgrade_map aliases by stripping leading/trailing whitespace.

        Runs before NotBlankStr validation so that ``" large "`` becomes
        ``"large"`` rather than being kept with surrounding spaces.
        Non-string or malformed entries are passed through unchanged so
        that Pydantic can surface a proper field-level ``ValidationError``.

        Returns:
            Result of type ``Any``.
        """
        if isinstance(data, dict) and "downgrade_map" in data:
            raw_map = data["downgrade_map"]
            if isinstance(raw_map, (list, tuple)):
                normalized: list[Any] = []
                for item in raw_map:
                    if (
                        isinstance(item, (list, tuple))
                        and len(item) == 2  # noqa: PLR2004
                        and isinstance(item[0], str)
                        and isinstance(item[1], str)
                    ):
                        normalized.append((item[0].strip(), item[1].strip()))
                    else:
                        normalized.append(item)
                return {
                    **data,
                    "downgrade_map": tuple(normalized),
                }
        return data

    @model_validator(mode="after")
    def _validate_downgrade_map(self) -> Self:
        """Validate downgrade_map for correctness.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        sources: list[str] = []
        for source, target in self.downgrade_map:
            if source == target:
                msg = f"Self-downgrade in downgrade_map: {source!r} -> {target!r}"
                raise ValueError(msg)
            sources.append(source)
        if len(sources) != len(set(sources)):
            dupes = sorted(s for s, c in Counter(sources).items() if c > 1)
            msg = f"Duplicate source aliases in downgrade_map: {dupes}"
            raise ValueError(msg)
        return self


class BudgetConfig(BaseModel):
    """Top-level budget configuration for a company.

    Defines the overall monthly budget, alert thresholds, per-task and
    per-agent spending limits, and automatic model downgrade settings.

    Attributes:
        total_monthly: Monthly budget limit.
        alerts: Alert threshold configuration.
        per_task_limit: Maximum cost per task.
        per_agent_daily_limit: Maximum cost per agent per day.
        auto_downgrade: Automatic model downgrade configuration.
        reset_day: Day of month when budget resets (1-28, avoids
            month-length issues).
        currency: ISO 4217 currency code for display formatting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="total_monthly",
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            parse=parse_float,
        ),
        MirrorField(
            field="per_task_limit",
            namespace=SettingNamespace.BUDGET,
            key="per_task_limit",
            parse=parse_float,
        ),
        MirrorField(
            field="per_agent_daily_limit",
            namespace=SettingNamespace.BUDGET,
            key="per_agent_daily_limit",
            parse=parse_float,
        ),
        MirrorField(
            field="reset_day",
            namespace=SettingNamespace.BUDGET,
            key="reset_day",
            parse=parse_int,
        ),
        MirrorField(
            field="currency",
            namespace=SettingNamespace.BUDGET,
            key="currency",
        ),
        MirrorField(
            field="forecast_required",
            namespace=SettingNamespace.BUDGET,
            key="forecast_required",
            parse=parse_bool,
        ),
        MirrorField(
            field="forecast_default_ceiling_multiplier",
            namespace=SettingNamespace.BUDGET,
            key="forecast_default_ceiling_multiplier",
            parse=parse_float,
        ),
        MirrorField(
            field="run_hard_ceiling",
            namespace=SettingNamespace.BUDGET,
            key="run_hard_ceiling",
            parse=parse_float,
        ),
        MirrorField(
            field="forecast_static_prior_per_turn_large",
            namespace=SettingNamespace.BUDGET,
            key="forecast_static_prior_per_turn_large",
            parse=parse_float,
        ),
        MirrorField(
            field="forecast_static_prior_per_turn_medium",
            namespace=SettingNamespace.BUDGET,
            key="forecast_static_prior_per_turn_medium",
            parse=parse_float,
        ),
        MirrorField(
            field="forecast_static_prior_per_turn_small",
            namespace=SettingNamespace.BUDGET,
            key="forecast_static_prior_per_turn_small",
            parse=parse_float,
        ),
        MirrorField(
            field="forecast_static_prior_per_turn_local_small",
            namespace=SettingNamespace.BUDGET,
            key="forecast_static_prior_per_turn_local_small",
            parse=parse_float,
        ),
        MirrorField(
            field="forecast_shrinkage_prior_weight",
            namespace=SettingNamespace.BUDGET,
            key="forecast_shrinkage_prior_weight",
            parse=parse_float,
        ),
    )

    total_monthly: float = Field(
        default=100.0,
        ge=0.0,
        description="Monthly budget limit",
    )
    alerts: BudgetAlertConfig = Field(
        default_factory=BudgetAlertConfig,
        description="Alert threshold configuration",
    )
    per_task_limit: float = Field(
        default=5.0,
        ge=0.0,
        description="Maximum cost per task",
    )
    per_agent_daily_limit: float = Field(
        default=10.0,
        ge=0.0,
        description="Maximum cost per agent per day",
    )
    auto_downgrade: AutoDowngradeConfig = Field(
        default_factory=AutoDowngradeConfig,
        description="Automatic model downgrade configuration",
    )
    reset_day: int = Field(
        default=1,
        ge=1,
        le=28,
        strict=True,
        description=(
            "Day of month when budget resets (1-28, avoids month-length issues)"
        ),
    )
    currency: CurrencyCode = Field(
        default=DEFAULT_CURRENCY,
        description=(
            "ISO 4217 currency code stamped onto every new cost record "
            "and used for display formatting. SynthOrg does not convert "
            "provider costs -- provider token prices are reported in the "
            "provider-native currency (see ``DEFAULT_CURRENCY``) and "
            "changing this setting relabels the code stamped onto "
            "subsequent records without translating any numeric values. "
            "Historical rows retain the code that was active when they "
            "were written."
        ),
    )
    risk_budget: RiskBudgetConfig = Field(
        default_factory=RiskBudgetConfig,
        description="Cumulative risk-unit action budget configuration",
    )
    pte_tracking_enabled: bool = Field(
        default=False,
        description="Enable Prefill Token Equivalents tracking (observability-only)",
    )
    forecast_required: bool = Field(
        default=True,
        description="Require operator approval of a pre-flight cost forecast",
    )
    forecast_default_ceiling_multiplier: float = Field(
        default=1.5,
        ge=1.0,
        description=(
            "Multiplier applied to the forecast upper bound when suggesting a"
            " per-run hard ceiling at approval time"
        ),
    )
    run_hard_ceiling: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Absolute hard real-money ceiling applied when Task.hard_ceiling"
            " is unset (zero disables the global fallback)"
        ),
    )
    forecast_static_prior_per_turn_large: float = Field(
        default=0.10,
        ge=0.0,
        description="Static prior cost per turn for the `large` model tier",
    )
    forecast_static_prior_per_turn_medium: float = Field(
        default=0.03,
        ge=0.0,
        description="Static prior cost per turn for the `medium` model tier",
    )
    forecast_static_prior_per_turn_small: float = Field(
        default=0.005,
        ge=0.0,
        description="Static prior cost per turn for the `small` model tier",
    )
    forecast_static_prior_per_turn_local_small: float = Field(
        default=0.0,
        ge=0.0,
        description="Static prior cost per turn for the `local-small` model tier",
    )
    forecast_shrinkage_prior_weight: float = Field(
        default=5.0,
        ge=0.0,
        description="Prior pseudo-count for the Bayesian shrinkage blend",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        """Apply mirrors.

        Returns:
            Result of type ``Any``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_per_task_limit_within_monthly(self) -> Self:
        """Ensure per_task_limit does not exceed total_monthly.

        When ``total_monthly`` is ``0.0``, per-task and per-agent limits
        are not validated against it.  A zero monthly budget means budget
        enforcement is disabled; limits are ignored at runtime.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.total_monthly > 0 and self.per_task_limit > self.total_monthly:
            msg = (
                f"per_task_limit ({self.per_task_limit}) "
                f"cannot exceed total_monthly ({self.total_monthly})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_per_agent_daily_limit_within_monthly(self) -> Self:
        """Ensure per_agent_daily_limit does not exceed total_monthly.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.total_monthly > 0 and self.per_agent_daily_limit > self.total_monthly:
            msg = (
                f"per_agent_daily_limit ({self.per_agent_daily_limit}) "
                f"cannot exceed total_monthly ({self.total_monthly})"
            )
            raise ValueError(msg)
        return self
