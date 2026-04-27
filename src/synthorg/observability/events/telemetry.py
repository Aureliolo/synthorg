"""Product telemetry event constants.

Two distinct namespaces live here:

* ``TELEMETRY_*`` constants are observability log event names emitted
  via ``logger.info(...)`` for the structured logging pipeline.
* ``TELEMETRY_EVENT_*`` constants are payload event types sent in
  ``TelemetryEvent.event_type`` to the telemetry backend.  They are
  the canonical strings shared by collector, scrubber allowlist,
  and analytics so all three reference one source of truth.
"""

from typing import Final

# Observability log event names.
TELEMETRY_HEARTBEAT_SENT: Final[str] = "telemetry.heartbeat.sent"
TELEMETRY_SESSION_SUMMARY_SENT: Final[str] = "telemetry.session_summary.sent"
TELEMETRY_REPORT_FAILED: Final[str] = "telemetry.report.failed"
TELEMETRY_PRIVACY_VIOLATION: Final[str] = "telemetry.privacy.violation"
TELEMETRY_ENABLED: Final[str] = "telemetry.enabled"
TELEMETRY_DISABLED: Final[str] = "telemetry.disabled"
TELEMETRY_REPORTER_INITIALIZED: Final[str] = "telemetry.reporter.initialized"
TELEMETRY_ENVIRONMENT_RESOLVED: Final[str] = "telemetry.environment.resolved"
TELEMETRY_DEPLOYMENT_ID_LOADED: Final[str] = "telemetry.deployment_id.loaded"
TELEMETRY_DEPLOYMENT_ID_CREATED: Final[str] = "telemetry.deployment_id.created"

# Telemetry payload event types (sent in TelemetryEvent.event_type).
TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT: Final[str] = "deployment.heartbeat"
TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY: Final[str] = "deployment.session_summary"
TELEMETRY_EVENT_DEPLOYMENT_STARTUP: Final[str] = "deployment.startup"
TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN: Final[str] = "deployment.shutdown"

# Event counter + subscription (META-MCP-2: feeds SignalsService telemetry aggregator).
TELEMETRY_SUBSCRIBER_FAILED: Final[str] = "telemetry.subscriber.failed"
TELEMETRY_COUNTER_EVICTED: Final[str] = "telemetry.counter.evicted"
TELEMETRY_COUNTER_RECORD_FAILED: Final[str] = "telemetry.counter.record_failed"
