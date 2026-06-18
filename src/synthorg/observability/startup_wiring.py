"""Observability startup wiring -- bridges sinks and collectors.

The audit-chain and OTLP log handlers intentionally do not know
about ``AppState``. At startup, this module walks the stdlib
logging handlers, finds the instances we care about, and connects
their export-outcome hooks into the process-wide Prometheus
collector. It also constructs the distributed :class:`TraceHandler`
from environment configuration and stashes it on ``AppState`` so
:mod:`synthorg.observability.tracing.instrumentation` can look up
the active tracer.

All wiring is idempotent: repeated calls leave the first registered
callback in place. Test-fixture startups re-run ``on_startup``, and
overwriting live callbacks mid-request would double-count metrics
or swap out the running trace handler.
"""

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.audit_chain.sink import AuditChainSink
from synthorg.observability.events.metrics import METRICS_PROMETHEUS_WIRING_SKIPPED
from synthorg.observability.events.tracing import (
    TRACE_CONFIG_INVALID_SAMPLING_RATIO,
    TRACE_HANDLER_INITIALIZED,
)
from synthorg.observability.http_handler import HttpBatchHandler
from synthorg.observability.metrics_hub import set_active_collector
from synthorg.observability.otlp_handler import OtlpHandler
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.observability.syslog_handler import CountingSysLogHandler
from synthorg.observability.tracing import (
    DisabledTraceConfig,
    OtlpHttpTraceConfig,
    TraceConfig,
    build_trace_handler,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.observability.prometheus_collector import PrometheusCollector

logger = get_logger(__name__)

_TRACE_ENDPOINT_ENV = "SYNTHORG_TRACE_OTLP_ENDPOINT"
_TRACE_SERVICE_NAME_ENV = "SYNTHORG_TRACE_SERVICE_NAME"
_TRACE_SAMPLING_RATIO_ENV = "SYNTHORG_TRACE_SAMPLING_RATIO"


def _iter_logging_handlers() -> list[logging.Handler]:
    """Return every handler attached anywhere in the logging hierarchy.

    Includes the root logger and every concrete ``logging.Logger``
    instance created so far. Sink wiring runs once at startup so
    the O(n) walk is not a hot path.
    """
    handlers: list[logging.Handler] = list(logging.getLogger().handlers)
    manager = logging.Logger.manager
    for logger_ref in manager.loggerDict.values():
        if isinstance(logger_ref, logging.Logger):
            handlers.extend(logger_ref.handlers)
    return handlers


def _load_trace_config() -> TraceConfig:
    """Resolve the trace config from environment variables.

    Returns :class:`DisabledTraceConfig` when no endpoint is set
    (the safe default: tracing is opt-in). When the endpoint is
    present, optional env vars control service name and sampling
    ratio; everything else uses :class:`OtlpHttpTraceConfig`
    defaults.

    Returns:
        A ``DisabledTraceConfig`` when no endpoint env var is set,
        otherwise an ``OtlpHttpTraceConfig`` populated from the
        environment.
    """
    endpoint = os.environ.get(_TRACE_ENDPOINT_ENV, "").strip()
    if not endpoint:
        return DisabledTraceConfig()
    service_name = os.environ.get(_TRACE_SERVICE_NAME_ENV, "synthorg").strip()
    sampling_ratio_raw = os.environ.get(_TRACE_SAMPLING_RATIO_ENV, "").strip()
    sampling_ratio = 1.0
    if sampling_ratio_raw:
        try:
            sampling_ratio = float(sampling_ratio_raw)
        except ValueError:
            logger.warning(
                TRACE_CONFIG_INVALID_SAMPLING_RATIO,
                rejected_value=sampling_ratio_raw,
                fallback=sampling_ratio,
            )
    return OtlpHttpTraceConfig(
        endpoint=endpoint,
        service_name=service_name or "synthorg",
        sampling_ratio=sampling_ratio,
    )


def _wire_prometheus_sinks(collector: PrometheusCollector) -> None:
    """Attach collector callbacks to every log handler that supports them.

    Each handler's setter is idempotent for the same callable, so
    repeated startup passes leave the live handler unchanged.
    """

    def _otlp_callback(outcome: str, dropped: int) -> None:
        collector.record_otlp_export(
            kind="logs",
            outcome=outcome,
            dropped_records=dropped,
        )

    def _audit_callback(
        status: str,
        chain_depth: int,
        timestamp_unix: float,
    ) -> None:
        collector.record_audit_append(
            status=status,
            chain_depth=chain_depth,
            timestamp_unix=timestamp_unix,
        )

    def _make_log_sink_callback(sink: str) -> Callable[[str, int], None]:
        def _callback(outcome: str, _dropped: int) -> None:
            collector.record_log_sink_export(sink=sink, outcome=outcome)

        return _callback

    for handler in _iter_logging_handlers():
        if isinstance(handler, OtlpHandler):
            handler.set_export_callback(_otlp_callback)
        elif isinstance(handler, AuditChainSink):
            handler.set_append_callback(_audit_callback)
        elif isinstance(handler, HttpBatchHandler):
            handler.set_export_callback(_make_log_sink_callback("http"))
        elif isinstance(handler, CountingSysLogHandler):
            handler.set_export_callback(_make_log_sink_callback("syslog"))


def wire_observability_callbacks(app_state: AppState) -> None:
    """Wire observability callbacks across AppState, sinks, and tracing.

    * Builds the :class:`TraceHandler` from environment config and
      stashes it on ``AppState`` so the tracing helpers resolve a
      live tracer on the first span.
    * Connects :class:`OtlpHandler` and :class:`AuditChainSink`
      export-outcome hooks to the Prometheus collector.

    Args:
        app_state: The configured :class:`AppState`.
    """
    from synthorg.observability.audit_chain.factory import (  # noqa: PLC0415
        install_audit_chain_sink,
    )

    # Attach the audit-chain sink BEFORE wiring Prometheus callbacks so the
    # collector hookup below finds the freshly-installed sink. No-op when
    # ``audit_chain.enabled`` is False (the default).
    install_audit_chain_sink(app_state.config.audit_chain, clock=app_state.clock)
    if app_state.slice(ObservabilityStateSlice).trace_handler is None:
        handler = build_trace_handler(_load_trace_config())
        app_state.swap_slice(
            app_state.slice(ObservabilityStateSlice).model_copy(
                update={"trace_handler": handler}
            )
        )
        logger.info(
            TRACE_HANDLER_INITIALIZED,
            kind=handler.__class__.__name__,
        )
    collector = app_state.slice(ObservabilityStateSlice).prometheus_collector
    if collector is not None:
        set_active_collector(collector)
        _wire_prometheus_sinks(collector)
    else:
        # No collector: OTLP / audit-chain / log-sink export-outcome
        # hooks are left unwired and their metrics silently never
        # record. Surface it so an operator missing the
        # ``synthorg_*_export_*`` series knows wiring was skipped
        # rather than the exporters being idle.
        logger.warning(METRICS_PROMETHEUS_WIRING_SKIPPED)
