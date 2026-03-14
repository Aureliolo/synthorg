"""Budget configuration models.

Implements the Cost Controls section of the Operations design page: alert
thresholds, per-task and per-agent limits, and automatic model downgrade.
"""

from collections import Counter
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class BudgetAlertConfig(BaseModel):
    """Alert threshold configuration for budget monitoring.

    Thresholds are expressed as percentages of the total monthly budget.
    They must be strictly ordered: ``warn_at < critical_at < hard_stop_at``.

    Attributes:
        warn_at: Percentage of budget that triggers a warning alert.
        critical_at: Percentage of budget that triggers a critical alert.
        hard_stop_at: Percentage of budget that triggers a hard stop.
    """

    model_config = ConfigDict(frozen=True)

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

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure thresholds are strictly ordered."""
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

    model_config = ConfigDict(frozen=True)

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
        """Validate downgrade_map for correctness."""
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
        total_monthly: Monthly budget in USD.
        alerts: Alert threshold configuration.
        per_task_limit: Maximum USD per task.
        per_agent_daily_limit: Maximum USD per agent per day.
        auto_downgrade: Automatic model downgrade configuration.
        reset_day: Day of month when budget resets (1-28, avoids
            month-length issues).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    total_monthly: float = Field(
        default=100.0,
        ge=0.0,
        description="Monthly budget in USD",
    )
    alerts: BudgetAlertConfig = Field(
        default_factory=BudgetAlertConfig,
        description="Alert threshold configuration",
    )
    per_task_limit: float = Field(
        default=5.0,
        ge=0.0,
        description="Maximum USD per task",
    )
    per_agent_daily_limit: float = Field(
        default=10.0,
        ge=0.0,
        description="Maximum USD per agent per day",
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

    @model_validator(mode="after")
    def _validate_per_task_limit_within_monthly(self) -> Self:
        """Ensure per_task_limit does not exceed total_monthly.

        When ``total_monthly`` is ``0.0``, per-task and per-agent limits
        are not validated against it.  A zero monthly budget means budget
        enforcement is disabled; limits are ignored at runtime.
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
        """Ensure per_agent_daily_limit does not exceed total_monthly."""
        if self.total_monthly > 0 and self.per_agent_daily_limit > self.total_monthly:
            msg = (
                f"per_agent_daily_limit ({self.per_agent_daily_limit}) "
                f"cannot exceed total_monthly ({self.total_monthly})"
            )
            raise ValueError(msg)
        return self
