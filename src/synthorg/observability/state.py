"""Observability feature state slice.

Holds the Prometheus metrics collector, the OTLP trace handler, and the
periodic audit-chain verification scheduler. All are ``None`` until wired
at boot; the metrics endpoint and WebSocket / middleware metric hooks raise
503 (or skip) on a ``None`` collector.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.observability.audit_chain.verify_scheduler import (
    AuditChainVerificationScheduler,
)
from synthorg.observability.prometheus_collector import (
    PrometheusCollector,
)
from synthorg.observability.tracing.protocol import TraceHandler


class ObservabilityStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the observability feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    prometheus_collector: PrometheusCollector | None = None
    trace_handler: TraceHandler | None = None
    audit_chain_verify_scheduler: AuditChainVerificationScheduler | None = None
