"""Risk budget configuration models.

Defines the configuration for cumulative risk-unit action budgets,
including per-task, per-agent daily, and total daily risk limits
with alert thresholds.

See the Operations design page (Risk Budget section).
"""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_float,
    parse_int,
)


class RiskBudgetAlertConfig(BaseModel):
    """Alert thresholds for risk budget utilization.

    Attributes:
        warn_at: Percentage at which to issue a warning.
        critical_at: Percentage at which to issue a critical alert.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="warn_at",
            namespace=SettingNamespace.BUDGET,
            key="risk_warn_pct",
            parse=parse_int,
        ),
        MirrorField(
            field="critical_at",
            namespace=SettingNamespace.BUDGET,
            key="risk_critical_pct",
            parse=parse_int,
        ),
    )

    warn_at: int = Field(default=75, ge=0, le=100, strict=True)
    critical_at: int = Field(default=90, ge=0, le=100, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply settings mirrors.

        Returns:
            Result of type ``object``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_ordering(self) -> Self:
        """Ensure warn_at < critical_at (strictly ordered).

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.warn_at >= self.critical_at:
            msg = (
                f"warn_at ({self.warn_at}) must be strictly less than "
                f"critical_at ({self.critical_at})"
            )
            raise ValueError(msg)
        return self


class RiskBudgetConfig(BaseModel):
    """Configuration for cumulative risk-unit action budgets.

    Opt-in via ``enabled: true``. When disabled, no risk tracking
    or enforcement occurs.

    Attributes:
        enabled: Whether risk budget tracking is active.
        per_task_risk_limit: Maximum risk units per task.
        per_agent_daily_risk_limit: Maximum risk units per agent per day.
        total_daily_risk_limit: Maximum total risk units per day.
        alerts: Alert threshold configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="per_task_risk_limit",
            namespace=SettingNamespace.BUDGET,
            key="risk_per_task_limit",
            parse=parse_float,
        ),
        MirrorField(
            field="per_agent_daily_risk_limit",
            namespace=SettingNamespace.BUDGET,
            key="risk_per_agent_daily",
            parse=parse_float,
        ),
        MirrorField(
            field="total_daily_risk_limit",
            namespace=SettingNamespace.BUDGET,
            key="risk_total_daily",
            parse=parse_float,
        ),
    )

    enabled: bool = False
    per_task_risk_limit: float = Field(default=5.0, ge=0.0)
    per_agent_daily_risk_limit: float = Field(default=20.0, ge=0.0)
    total_daily_risk_limit: float = Field(default=100.0, ge=0.0)
    alerts: RiskBudgetAlertConfig = Field(
        default_factory=RiskBudgetAlertConfig,
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply settings mirrors.

        Returns:
            Result of type ``object``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_limits(self) -> Self:
        """Ensure limit hierarchy: per_task <= per_agent_daily <= total_daily.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if (
            self.per_agent_daily_risk_limit > 0
            and self.per_task_risk_limit > self.per_agent_daily_risk_limit
        ):
            msg = (
                f"per_task_risk_limit ({self.per_task_risk_limit}) "
                f"must be <= per_agent_daily_risk_limit "
                f"({self.per_agent_daily_risk_limit})"
            )
            raise ValueError(msg)
        if self.total_daily_risk_limit > 0:
            if self.per_task_risk_limit > self.total_daily_risk_limit:
                msg = (
                    f"per_task_risk_limit ({self.per_task_risk_limit}) "
                    f"must be <= total_daily_risk_limit "
                    f"({self.total_daily_risk_limit})"
                )
                raise ValueError(msg)
            if self.per_agent_daily_risk_limit > self.total_daily_risk_limit:
                msg = (
                    f"per_agent_daily_risk_limit "
                    f"({self.per_agent_daily_risk_limit}) must be <= "
                    f"total_daily_risk_limit "
                    f"({self.total_daily_risk_limit})"
                )
                raise ValueError(msg)
        return self
