"""Typed argument models for analytics tools.

Tools wired to consume these models:

* :class:`~synthorg.tools.analytics.data_aggregator.DataAggregatorTool`
  -> :class:`DataAggregatorArgs`
* :class:`~synthorg.tools.analytics.metric_collector.MetricCollectorTool`
  -> :class:`MetricCollectorArgs`
* :class:`~synthorg.tools.analytics.report_generator.ReportGeneratorTool`
  -> :class:`ReportGeneratorArgs`
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


AggregationPeriod = Literal["7d", "30d", "90d", "custom"]
AggregationGroupBy = Literal["day", "week", "month", "agent", "department"]
ReportType = Literal[
    "budget_summary", "performance", "trend_analysis", "cost_breakdown"
]
ReportPeriod = Literal["7d", "30d", "90d", "ytd"]
ReportOutputFormat = Literal["text", "markdown", "json"]


class DataAggregatorArgs(BaseModel):
    """Args for ``data_aggregator``.

    The ``period == 'custom'`` cross-field constraint (start_date /
    end_date both required) is enforced via a model validator so the
    LLM-facing message references the param names directly.
    """

    model_config = _ARGS_CONFIG

    metrics: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Metric names to aggregate",
    )
    period: AggregationPeriod = Field(description="Time period for aggregation")
    group_by: AggregationGroupBy | None = Field(
        default=None,
        description="Optional grouping dimension",
    )
    start_date: NotBlankStr | None = Field(
        default=None,
        description="Start date for custom period (ISO 8601)",
    )
    end_date: NotBlankStr | None = Field(
        default=None,
        description="End date for custom period (ISO 8601)",
    )

    @model_validator(mode="after")
    def _custom_period_requires_dates(self) -> Self:
        if self.period == "custom" and (
            self.start_date is None or self.end_date is None
        ):
            msg = "period='custom' requires both start_date and end_date to be provided"
            raise ValueError(msg)
        return self


class MetricCollectorArgs(BaseModel):
    """Args for ``metric_collector``."""

    model_config = _ARGS_CONFIG

    metric_name: NotBlankStr = Field(description="Name of the metric to record")
    value: float = Field(description="Metric value")
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Optional key-value tags",
    )
    unit: NotBlankStr | None = Field(
        default=None,
        description="Optional measurement unit (e.g. 'seconds', 'bytes')",
    )


class ReportGeneratorArgs(BaseModel):
    """Args for ``report_generator``."""

    model_config = _ARGS_CONFIG

    report_type: ReportType = Field(description="Type of report to generate")
    period: ReportPeriod = Field(description="Reporting period")
    format: ReportOutputFormat = Field(default="markdown", description="Output format")


__all__ = [
    "AggregationGroupBy",
    "AggregationPeriod",
    "DataAggregatorArgs",
    "MetricCollectorArgs",
    "ReportGeneratorArgs",
    "ReportOutputFormat",
    "ReportPeriod",
    "ReportType",
]
