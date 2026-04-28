"""Metrics event constants for Prometheus and OTLP telemetry.

Event taxonomy for the metrics collection and export subsystem.
"""

from typing import Final

# Prometheus scrape events
METRICS_SCRAPE_COMPLETED: Final[str] = "metrics.scrape.completed"
METRICS_SCRAPE_FAILED: Final[str] = "metrics.scrape.failed"
METRICS_COLLECTOR_INITIALIZED: Final[str] = "metrics.collector.initialized"

# Coordination metrics push events
METRICS_COORDINATION_RECORDED: Final[str] = "metrics.coordination.recorded"

# OTLP export events
METRICS_OTLP_EXPORT_COMPLETED: Final[str] = "metrics.otlp.export_completed"
METRICS_OTLP_EXPORT_FAILED: Final[str] = "metrics.otlp.export_failed"
METRICS_OTLP_FLUSHER_STARTED: Final[str] = "metrics.otlp.flusher_started"
METRICS_OTLP_FLUSHER_STOPPED: Final[str] = "metrics.otlp.flusher_stopped"
METRICS_OTLP_FLUSHER_ERROR: Final[str] = "metrics.otlp.flusher_error"
METRICS_OTLP_CALLBACK_ERROR: Final[str] = "metrics.otlp.callback_error"
METRICS_OTLP_INVALID_CALLBACK: Final[str] = "metrics.otlp.invalid_callback"

# Prometheus collector lifecycle
METRICS_COLLECTOR_ACTIVATED: Final[str] = "metrics.collector.activated"
METRICS_COLLECTOR_DEACTIVATED: Final[str] = "metrics.collector.deactivated"

# Prometheus recording / validation failures (distinct from scrape failures)
METRICS_RECORD_FAILED: Final[str] = "metrics.record.failed"
API_REQUEST_VALIDATION_FAILED: Final[str] = "metrics.api_request.validation_failed"

# Client transport disconnect (SSE / WebSocket / MCP stdio); recorded only
# after the corresponding ``synthorg_client_disconnects_total`` increment
# succeeds so the metric and the log stay in lockstep.
CLIENT_DISCONNECTED: Final[str] = "metrics.client_disconnected"

# Clock-skew clamp on a duration computation; emitted when ``now`` precedes
# ``created_at`` and the metric value is forced to 0 to keep the histogram
# bucket from absorbing a phantom 0-second sample.
METRICS_CLOCK_SKEW_DETECTED: Final[str] = "metrics.clock_skew_detected"
