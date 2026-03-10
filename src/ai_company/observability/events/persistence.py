"""Persistence event constants for structured logging.

Constants follow the ``persistence.<entity>.<action>`` naming convention
and are passed as the first argument to ``logger.info()``/``logger.debug()``
calls in the persistence layer.
"""

from typing import Final

PERSISTENCE_BACKEND_CONNECTING: Final[str] = "persistence.backend.connecting"
PERSISTENCE_BACKEND_CONNECTED: Final[str] = "persistence.backend.connected"
PERSISTENCE_BACKEND_CONNECTION_FAILED: Final[str] = (
    "persistence.backend.connection_failed"
)
PERSISTENCE_BACKEND_ALREADY_CONNECTED: Final[str] = (
    "persistence.backend.already_connected"
)
PERSISTENCE_BACKEND_DISCONNECTING: Final[str] = "persistence.backend.disconnecting"
PERSISTENCE_BACKEND_DISCONNECTED: Final[str] = "persistence.backend.disconnected"
PERSISTENCE_BACKEND_DISCONNECT_ERROR: Final[str] = (
    "persistence.backend.disconnect_error"
)
PERSISTENCE_BACKEND_HEALTH_CHECK: Final[str] = "persistence.backend.health_check"
PERSISTENCE_BACKEND_CREATED: Final[str] = "persistence.backend.created"
PERSISTENCE_BACKEND_UNKNOWN: Final[str] = "persistence.backend.unknown"
PERSISTENCE_BACKEND_WAL_MODE_FAILED: Final[str] = "persistence.backend.wal_mode_failed"
PERSISTENCE_BACKEND_NOT_CONNECTED: Final[str] = "persistence.backend.not_connected"

PERSISTENCE_MIGRATION_STARTED: Final[str] = "persistence.migration.started"
PERSISTENCE_MIGRATION_COMPLETED: Final[str] = "persistence.migration.completed"
PERSISTENCE_MIGRATION_SKIPPED: Final[str] = "persistence.migration.skipped"
PERSISTENCE_MIGRATION_FAILED: Final[str] = "persistence.migration.failed"

PERSISTENCE_TASK_SAVED: Final[str] = "persistence.task.saved"
PERSISTENCE_TASK_SAVE_FAILED: Final[str] = "persistence.task.save_failed"
PERSISTENCE_TASK_FETCHED: Final[str] = "persistence.task.fetched"
PERSISTENCE_TASK_FETCH_FAILED: Final[str] = "persistence.task.fetch_failed"
PERSISTENCE_TASK_LISTED: Final[str] = "persistence.task.listed"
PERSISTENCE_TASK_LIST_FAILED: Final[str] = "persistence.task.list_failed"
PERSISTENCE_TASK_DELETED: Final[str] = "persistence.task.deleted"
PERSISTENCE_TASK_DELETE_FAILED: Final[str] = "persistence.task.delete_failed"

PERSISTENCE_COST_RECORD_SAVED: Final[str] = "persistence.cost_record.saved"
PERSISTENCE_COST_RECORD_SAVE_FAILED: Final[str] = "persistence.cost_record.save_failed"
PERSISTENCE_COST_RECORD_QUERIED: Final[str] = "persistence.cost_record.queried"
PERSISTENCE_COST_RECORD_QUERY_FAILED: Final[str] = (
    "persistence.cost_record.query_failed"
)
PERSISTENCE_COST_RECORD_AGGREGATED: Final[str] = "persistence.cost_record.aggregated"
PERSISTENCE_COST_RECORD_AGGREGATE_FAILED: Final[str] = (
    "persistence.cost_record.aggregate_failed"
)

PERSISTENCE_TASK_DESERIALIZE_FAILED: Final[str] = "persistence.task.deserialize_failed"

PERSISTENCE_MESSAGE_SAVED: Final[str] = "persistence.message.saved"
PERSISTENCE_MESSAGE_SAVE_FAILED: Final[str] = "persistence.message.save_failed"
PERSISTENCE_MESSAGE_DUPLICATE: Final[str] = "persistence.message.duplicate"
PERSISTENCE_MESSAGE_HISTORY_FETCHED: Final[str] = "persistence.message.history_fetched"
PERSISTENCE_MESSAGE_HISTORY_FAILED: Final[str] = "persistence.message.history_failed"
PERSISTENCE_MESSAGE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.message.deserialize_failed"
)

PERSISTENCE_LIFECYCLE_EVENT_SAVED: Final[str] = "persistence.lifecycle_event.saved"
PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED: Final[str] = (
    "persistence.lifecycle_event.save_failed"
)
PERSISTENCE_LIFECYCLE_EVENT_LISTED: Final[str] = "persistence.lifecycle_event.listed"
PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED: Final[str] = (
    "persistence.lifecycle_event.list_failed"
)
PERSISTENCE_LIFECYCLE_EVENT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.lifecycle_event.deserialize_failed"
)

PERSISTENCE_TASK_METRIC_SAVED: Final[str] = "persistence.task_metric.saved"
PERSISTENCE_TASK_METRIC_SAVE_FAILED: Final[str] = "persistence.task_metric.save_failed"
PERSISTENCE_TASK_METRIC_QUERIED: Final[str] = "persistence.task_metric.queried"
PERSISTENCE_TASK_METRIC_QUERY_FAILED: Final[str] = (
    "persistence.task_metric.query_failed"
)
PERSISTENCE_TASK_METRIC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.task_metric.deserialize_failed"
)

PERSISTENCE_COLLAB_METRIC_SAVED: Final[str] = "persistence.collab_metric.saved"
PERSISTENCE_COLLAB_METRIC_SAVE_FAILED: Final[str] = (
    "persistence.collab_metric.save_failed"
)
PERSISTENCE_COLLAB_METRIC_QUERIED: Final[str] = "persistence.collab_metric.queried"
PERSISTENCE_COLLAB_METRIC_QUERY_FAILED: Final[str] = (
    "persistence.collab_metric.query_failed"
)
PERSISTENCE_COLLAB_METRIC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.collab_metric.deserialize_failed"
)

# Parked context events
PERSISTENCE_PARKED_CONTEXT_SAVED: Final[str] = "persistence.parked_context.saved"
PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED: Final[str] = (
    "persistence.parked_context.save_failed"
)
PERSISTENCE_PARKED_CONTEXT_QUERIED: Final[str] = "persistence.parked_context.queried"
PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED: Final[str] = (
    "persistence.parked_context.query_failed"
)
PERSISTENCE_PARKED_CONTEXT_NOT_FOUND: Final[str] = (
    "persistence.parked_context.not_found"
)
PERSISTENCE_PARKED_CONTEXT_DELETED: Final[str] = "persistence.parked_context.deleted"
PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.parked_context.deserialize_failed"
)
