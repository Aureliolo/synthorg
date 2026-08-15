"""Budget configuration models.

Implements the Cost Controls section of ``docs/design/budget.md``: alert
thresholds, per-task and per-agent limits, and risk budget configuration.
"""

from collections.abc import Mapping
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.call_analytics_config import CallAnalyticsConfig
from synthorg.budget.config_mirrors import BUDGET_MIRROR_FIELDS
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.quota import SubscriptionConfig
from synthorg.budget.risk_config import RiskBudgetConfig
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
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
    def _apply_mirrors(cls, data: object) -> object:
        """Apply mirrors.

        Returns:
            Result of type ``object``.
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


class BudgetConfig(BaseModel):
    """Top-level budget configuration for a company.

    Defines the overall monthly budget, alert thresholds, and per-task /
    per-agent spending limits. Every knob here refuses spend; none of them
    re-points an agent at a different model, which is the operator's
    binding and is never rewritten from a budget signal.

    Attributes:
        total_monthly: Monthly budget limit.
        alerts: Alert threshold configuration.
        per_task_limit: Maximum cost per task.
        per_agent_daily_limit: Maximum cost per agent per day.
        reset_day: Day of month when budget resets (1-28, avoids
            month-length issues).
        currency: ISO 4217 currency code for display formatting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = BUDGET_MIRROR_FIELDS

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
    subscriptions: Mapping[str, SubscriptionConfig] = Field(
        default_factory=dict,
        description=(
            "Per-provider subscription / quota configuration consumed by the "
            "quota tracker. Providers without an entry are quota-unbounded."
        ),
    )
    call_analytics: CallAnalyticsConfig = Field(
        default_factory=CallAnalyticsConfig,
        description="Per-call analytics aggregation + retry-rate alert config",
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
        default=25.0,
        ge=0.0,
        description=(
            "Absolute hard real-money ceiling applied when Task.hard_ceiling"
            " is unset. The shipped default 25.0 is a safety net; 0.0 is the"
            " explicit opt-out that disables the global fallback"
        ),
    )
    run_hard_token_ceiling: int = Field(
        default=50_000_000,
        ge=0,
        description=(
            "Absolute hard token ceiling applied when Task.hard_token_ceiling"
            " is unset. The money ceiling measures nothing against a flat-rate"
            " provider, where cost never rises; tokens are measured on every"
            " provider. 0 is the explicit opt-out"
        ),
    )
    session_token_ceiling: int = Field(
        default=2_000_000,
        ge=0,
        description=(
            "Absolute hard token ceiling for the short helper sessions, each"
            " of which carries its own tuned money ceiling that measures"
            " nothing against a flat-rate provider. 0 is the explicit opt-out"
        ),
    )
    forecast_static_prior_per_turn_expert: float = Field(
        default=0.10,
        ge=0.0,
        description="Static prior cost per turn for an `expert` model",
    )
    forecast_static_prior_per_turn_capable: float = Field(
        default=0.03,
        ge=0.0,
        description="Static prior cost per turn for a `capable` model",
    )
    forecast_static_prior_per_turn_basic: float = Field(
        default=0.005,
        ge=0.0,
        description="Static prior cost per turn for a `basic` model",
    )
    # Locality, not capability: an operator hosting the model pays no
    # per-token price whatever rung it sits on, so this prior overrides the
    # three above rather than being a fourth rung among them.
    forecast_static_prior_per_turn_local: float = Field(
        default=0.0,
        ge=0.0,
        description="Static prior cost per turn for a locally-hosted model",
    )
    forecast_shrinkage_prior_weight: float = Field(
        default=5.0,
        ge=0.0,
        description="Prior pseudo-count for the Bayesian shrinkage blend",
    )
    benchmark_provider: Literal["measured"] = Field(
        default="measured",
        description=(
            "Source of per-model benchmark scores for the Pareto frontier"
            " and stakes-routing floors: `measured` reads repository-backed"
            " scores (seeded at boot from the committed recording artifact);"
            " a model with no measured score is shown as absent, never faked"
        ),
    )
    model_capability_overrides: Mapping[NotBlankStr, CapabilityLevel] = Field(
        default_factory=dict,
        description=(
            "Operator map of model id to capability rung, consulted by the"
            " Pareto downgrade traversal before the built-in archetype"
            " heuristic. Values are typed against the canonical rungs, so"
            " a non-canonical one is rejected at config construction"
            " rather than slipping through to wiring."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply mirrors.

        Returns:
            Result of type ``object``.
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
