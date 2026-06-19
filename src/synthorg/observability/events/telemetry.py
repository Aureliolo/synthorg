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
TELEMETRY_REPORTER_CONFIGURE_FAILED: Final[str] = "telemetry.reporter.configure_failed"
# Emitted ONCE at startup when telemetry is enabled but the build artifact
# ships a sentinel token instead of the embedded write-only project token.
# This is a build-time misconfiguration, not a runtime issue; surfacing it
# loudly at boot lets operators escalate to the build pipeline rather than
# silently degrading to a noop reporter.
TELEMETRY_TOKEN_MISSING: Final[str] = "telemetry.token.missing"  # noqa: S105
TELEMETRY_ENVIRONMENT_RESOLVED: Final[str] = "telemetry.environment.resolved"
# Emitted when an existing deployment ID is read from disk during start().
TELEMETRY_DEPLOYMENT_ID_LOADED: Final[str] = "telemetry.deployment_id.loaded"
# Emitted when a fresh deployment ID is atomically written to disk during start()
# (this replica won the O_CREAT|O_EXCL race or no peer file existed).
TELEMETRY_DEPLOYMENT_ID_CREATED: Final[str] = "telemetry.deployment_id.created"
# Emitted when shutdown() runs on an enabled collector that never had start()
# called (or whose start() failed before the deployment ID loaded). Surfaces
# the silent-init-failure path so operators have a log signal.
TELEMETRY_SHUTDOWN_WITHOUT_START: Final[str] = "telemetry.shutdown.without_start"
# Emitted when shutdown() flips the collector into its terminal state. After
# this event a subsequent start() raises rather than silently reusing a
# torn-down reporter.
TELEMETRY_CLOSED: Final[str] = "telemetry.closed"

# Telemetry payload event types (sent in TelemetryEvent.event_type).
TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT: Final[str] = "deployment.heartbeat"
TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY: Final[str] = "deployment.session_summary"
TELEMETRY_EVENT_DEPLOYMENT_STARTUP: Final[str] = "deployment.startup"
TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN: Final[str] = "deployment.shutdown"

# Event counter + subscription (feed the SignalsService telemetry aggregator).
TELEMETRY_SUBSCRIBER_FAILED: Final[str] = "telemetry.subscriber.failed"
TELEMETRY_COUNTER_EVICTED: Final[str] = "telemetry.counter.evicted"
TELEMETRY_COUNTER_RECORD_FAILED: Final[str] = "telemetry.counter.record_failed"
