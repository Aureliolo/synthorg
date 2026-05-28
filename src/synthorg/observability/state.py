"""Observability feature state slice.

Holds the Prometheus metrics collector and the OTLP trace handler. Both are
``None`` until wired at boot; the metrics endpoint and WebSocket / middleware
metric hooks raise 503 (or skip) on a ``None`` collector.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.observability.prometheus_collector import (
    PrometheusCollector,
)
from synthorg.observability.tracing.protocol import TraceHandler


class ObservabilityStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the observability feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prometheus_collector: PrometheusCollector | None = None
    trace_handler: TraceHandler | None = None
