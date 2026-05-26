"""Automated reporting configuration models.

Defines report periods, template names, and scheduling configuration
for the automated reporting system.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportPeriod(StrEnum):
    """Time period for report generation.

    Members:
        DAILY: Previous day (00:00 UTC to 00:00 UTC).
        WEEKLY: Previous week (Monday to Monday, UTC).
        MONTHLY: Previous month (1st to 1st, UTC).
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReportTemplateName(StrEnum):
    """Available report template types.

    Members:
        SPENDING_SUMMARY: Cost and spending breakdown.
        PERFORMANCE_METRICS: Agent performance metrics.
        TASK_COMPLETION: Task completion rates.
        RISK_TRENDS: Risk accumulation trends.
        COMPREHENSIVE: All templates combined.
    """

    SPENDING_SUMMARY = "spending_summary"
    PERFORMANCE_METRICS = "performance_metrics"
    TASK_COMPLETION = "task_completion"
    RISK_TRENDS = "risk_trends"
    COMPREHENSIVE = "comprehensive"


class ReportScheduleConfig(BaseModel):
    """Schedule configuration for automated report generation.

    Attributes:
        enabled: Whether automated report generation is active.
        periods: Which time periods to auto-generate reports for.
        templates: Which report templates to include.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    periods: tuple[ReportPeriod, ...] = ()
    templates: tuple[ReportTemplateName, ...] = (ReportTemplateName.COMPREHENSIVE,)

    @model_validator(mode="after")
    def _validate_unique_periods(self) -> Self:
        """Ensure no duplicate periods.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if len(self.periods) != len(set(self.periods)):
            msg = "Duplicate periods in schedule configuration"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_templates(self) -> Self:
        """Ensure no duplicate templates.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if len(self.templates) != len(set(self.templates)):
            msg = "Duplicate templates in schedule configuration"
            raise ValueError(msg)
        return self


class AutomatedReportingConfig(BaseModel):
    """Top-level configuration for the automated reporting system.

    Attributes:
        schedule: Report scheduling configuration.
        retention_days: How long to keep generated reports.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schedule: ReportScheduleConfig = Field(
        default_factory=ReportScheduleConfig,
    )
    retention_days: int = Field(default=90, ge=1, le=365, strict=True)
