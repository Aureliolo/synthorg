"""Models for the analytics read service.

The analytics facade is a view layer on top of :class:`SignalsService`
snapshots; its response types live here so handlers and documentation
share one schema.  All models are frozen and reject ``NaN`` / ``Inf``
values (per CLAUDE.md), and the roll-up helpers emit them directly.
"""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.core.types import NotBlankStr
from synthorg.meta.signal_models import TrendDirection


class AnalyticsOverview(BaseModel):
    """Headline numbers distilled from the composite signal snapshot.

    Attributes:
        avg_quality_score: Org-wide average quality (0-10).
        avg_success_rate: Org-wide success rate (0-1).
        total_spend: Total spend in the window (``currency``).
        currency: ISO 4217 currency code for ``total_spend``.
        days_until_budget_exhausted: Forecast runway.
        total_error_findings: Count of error findings in the window.
        total_proposals: Count of evolution proposals in the window.
        collected_at: When the overview was assembled.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    avg_quality_score: float = Field(ge=0.0, le=10.0)
    avg_success_rate: float = Field(ge=0.0, le=1.0)
    total_spend: float = Field(ge=0.0)
    currency: CurrencyCode = DEFAULT_CURRENCY
    days_until_budget_exhausted: int | None = None
    total_error_findings: int = Field(ge=0)
    total_proposals: int = Field(ge=0)
    collected_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )


class MetricTrend(BaseModel):
    """Single-metric trend entry.

    Attributes:
        name: Metric name.
        current_value: Most-recent aggregate value.
        direction: Trend direction relative to the prior window.
        window_days: Observation window length.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    current_value: float
    direction: TrendDirection
    window_days: int = Field(ge=1)


class AnalyticsTrends(BaseModel):
    """Batch of metric trends for a given window."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    metrics: tuple[MetricTrend, ...] = ()
    window_days: int = Field(ge=1)


class AnalyticsForecast(BaseModel):
    """Budget forecast derived from the current signals snapshot.

    Attributes:
        horizon_days: Forecast horizon.
        days_until_budget_exhausted: Days until the current window's
            spend pace exhausts the configured budget; ``None`` when
            the window cannot be projected confidently.
        confidence: Forecast confidence (0-1).
        projected_spend: Linear projection of spend across the horizon.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    horizon_days: int = Field(ge=1)
    days_until_budget_exhausted: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    projected_spend: float = Field(ge=0.0)
    currency: CurrencyCode = DEFAULT_CURRENCY


class MetricsSnapshot(BaseModel):
    """Current-value metrics pulled per-request from aggregators.

    Mirrors the ``synthorg_metrics_get_current`` response: a flat
    mapping of metric-name -> current value for the requested filter.
    Metric names must be non-blank strings; callers keying on blank
    names would silently collide downstream.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    metrics: dict[NotBlankStr, float] = Field(default_factory=dict)
    captured_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )


class MetricsHistoryPoint(BaseModel):
    """One point in a metrics-history response.

    ``values`` is keyed by metric name and rejects blank/whitespace-only
    keys for the same reason as :class:`MetricsSnapshot.metrics`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    timestamp: AwareDatetime
    values: dict[NotBlankStr, float] = Field(default_factory=dict)


class MetricsHistory(BaseModel):
    """Historical samples for a metric-name set."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    metric_names: tuple[NotBlankStr, ...] = ()
    points: tuple[MetricsHistoryPoint, ...] = ()
