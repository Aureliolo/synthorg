"""Cross-deployment analytics event constants for structured logging.

Constants follow the ``cross_deployment.<subject>.<action>`` naming
convention and are passed as the first argument to structured log calls.
"""

from typing import Final

# -- Emitter lifecycle -----------------------------------------------------

XDEPLOY_EMITTER_INITIALIZED: Final[str] = "cross_deployment.emitter.initialized"
XDEPLOY_EMITTER_CLOSED: Final[str] = "cross_deployment.emitter.closed"

# -- Event emission --------------------------------------------------------

XDEPLOY_EVENT_QUEUED: Final[str] = "cross_deployment.event.queued"
XDEPLOY_EVENT_EMIT_FAILED: Final[str] = "cross_deployment.event.emit_failed"

# -- Batch flushing --------------------------------------------------------

XDEPLOY_BATCH_FLUSHED: Final[str] = "cross_deployment.batch.flushed"
XDEPLOY_BATCH_FLUSH_FAILED: Final[str] = "cross_deployment.batch.flush_failed"
XDEPLOY_BATCH_FLUSH_RETRYING: Final[str] = "cross_deployment.batch.flush_retrying"
XDEPLOY_BATCH_DROPPED: Final[str] = "cross_deployment.batch.dropped"
XDEPLOY_RESPONSE_BODY_UNREADABLE: Final[str] = (
    "cross_deployment.response.body_unreadable"
)

# -- Collector -------------------------------------------------------------

XDEPLOY_COLLECTOR_INGESTED: Final[str] = "cross_deployment.collector.ingested"
XDEPLOY_COLLECTOR_INGEST_FAILED: Final[str] = "cross_deployment.collector.ingest_failed"

# -- Pattern aggregation ---------------------------------------------------

XDEPLOY_PATTERN_DETECTED: Final[str] = "cross_deployment.pattern.detected"

# -- Recommendations -------------------------------------------------------

XDEPLOY_RECOMMENDATION_GENERATED: Final[str] = (
    "cross_deployment.recommendation.generated"
)
