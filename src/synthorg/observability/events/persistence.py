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
PERSISTENCE_MIGRATION_FAILED: Final[str] = "persistence.migration.failed"

PERSISTENCE_TASK_SAVED: Final[str] = "persistence.task.saved"
PERSISTENCE_TASK_SAVE_FAILED: Final[str] = "persistence.task.save_failed"
PERSISTENCE_TASK_FETCHED: Final[str] = "persistence.task.fetched"
PERSISTENCE_TASK_FETCH_FAILED: Final[str] = "persistence.task.fetch_failed"
PERSISTENCE_TASK_LISTED: Final[str] = "persistence.task.listed"
PERSISTENCE_TASK_LIST_FAILED: Final[str] = "persistence.task.list_failed"
PERSISTENCE_TASK_COUNTED: Final[str] = "persistence.task.counted"
PERSISTENCE_TASK_COUNT_FAILED: Final[str] = "persistence.task.count_failed"
PERSISTENCE_TASK_DELETED: Final[str] = "persistence.task.deleted"
PERSISTENCE_TASK_DELETE_FAILED: Final[str] = "persistence.task.delete_failed"

PERSISTENCE_COST_RECORD_SAVED: Final[str] = "persistence.cost_record.saved"
PERSISTENCE_COST_RECORD_SAVE_FAILED: Final[str] = "persistence.cost_record.save_failed"
PERSISTENCE_COST_RECORD_QUERIED: Final[str] = "persistence.cost_record.queried"
PERSISTENCE_COST_RECORD_QUERY_FAILED: Final[str] = (
    "persistence.cost_record.query_failed"
)
PERSISTENCE_COST_RECORD_AGGREGATED: Final[str] = "persistence.cost_record.aggregated"

PERSISTENCE_COST_FORECAST_SAVED: Final[str] = "persistence.cost_forecast.saved"
PERSISTENCE_COST_FORECAST_FETCHED: Final[str] = "persistence.cost_forecast.fetched"
PERSISTENCE_COST_FORECAST_LISTED: Final[str] = "persistence.cost_forecast.listed"
PERSISTENCE_COST_FORECAST_FAILED: Final[str] = "persistence.cost_forecast.failed"
PERSISTENCE_COST_RECORD_AGGREGATE_FAILED: Final[str] = (
    "persistence.cost_record.aggregate_failed"
)

PERSISTENCE_TASK_DESERIALIZE_FAILED: Final[str] = "persistence.task.deserialize_failed"

PERSISTENCE_FLIGHT_RECORDER_SAVED: Final[str] = "persistence.flight_recorder.saved"
PERSISTENCE_FLIGHT_RECORDER_SAVE_FAILED: Final[str] = (
    "persistence.flight_recorder.save_failed"
)
PERSISTENCE_FLIGHT_RECORDER_QUERIED: Final[str] = "persistence.flight_recorder.queried"
PERSISTENCE_FLIGHT_RECORDER_QUERY_FAILED: Final[str] = (
    "persistence.flight_recorder.query_failed"
)
PERSISTENCE_FLIGHT_RECORDER_DELETE_FAILED: Final[str] = (
    "persistence.flight_recorder.delete_failed"
)
PERSISTENCE_FLIGHT_RECORDER_DESERIALIZE_FAILED: Final[str] = (
    "persistence.flight_recorder.deserialize_failed"
)

PERSISTENCE_MESSAGE_SAVED: Final[str] = "persistence.message.saved"
PERSISTENCE_MESSAGE_SAVE_FAILED: Final[str] = "persistence.message.save_failed"
PERSISTENCE_MESSAGE_DUPLICATE: Final[str] = "persistence.message.duplicate"
PERSISTENCE_MESSAGE_HISTORY_FETCHED: Final[str] = "persistence.message.history_fetched"
PERSISTENCE_MESSAGE_HISTORY_FAILED: Final[str] = "persistence.message.history_failed"
PERSISTENCE_MESSAGE_FETCHED: Final[str] = "persistence.message.fetched"
PERSISTENCE_MESSAGE_FETCH_FAILED: Final[str] = "persistence.message.fetch_failed"
PERSISTENCE_MESSAGE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.message.deserialize_failed"
)
PERSISTENCE_MESSAGE_DELETED: Final[str] = "persistence.message.deleted"
PERSISTENCE_MESSAGE_DELETE_FAILED: Final[str] = "persistence.message.delete_failed"

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
PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED: Final[str] = (
    "persistence.mcp_installation.save_failed"
)
PERSISTENCE_MCP_INSTALLATION_DELETE_FAILED: Final[str] = (
    "persistence.mcp_installation.delete_failed"
)
PERSISTENCE_MCP_INSTALLATION_LIST_FAILED: Final[str] = (
    "persistence.mcp_installation.list_failed"
)
PERSISTENCE_PARKED_CONTEXT_DELETED: Final[str] = "persistence.parked_context.deleted"
PERSISTENCE_PARKED_CONTEXT_DELETE_FAILED: Final[str] = (
    "persistence.parked_context.delete_failed"
)
PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.parked_context.deserialize_failed"
)

# Audit entry events
PERSISTENCE_AUDIT_ENTRY_SAVED: Final[str] = "persistence.audit_entry.saved"
PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED: Final[str] = "persistence.audit_entry.save_failed"
PERSISTENCE_AUDIT_ENTRY_QUERIED: Final[str] = "persistence.audit_entry.queried"
PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED: Final[str] = (
    "persistence.audit_entry.query_failed"
)
PERSISTENCE_AUDIT_ENTRY_DESERIALIZE_FAILED: Final[str] = (
    "persistence.audit_entry.deserialize_failed"
)

# TimescaleDB hypertable events
PERSISTENCE_TIMESCALEDB_UNAVAILABLE: Final[str] = "persistence.timescaledb.unavailable"
PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED: Final[str] = (
    "persistence.timescaledb.hypertable_created"
)
PERSISTENCE_TIMESCALEDB_SETUP_FAILED: Final[str] = (
    "persistence.timescaledb.setup_failed"
)

# Decision record events
PERSISTENCE_DECISION_RECORD_SAVED: Final[str] = "persistence.decision_record.saved"
PERSISTENCE_DECISION_RECORD_SAVE_FAILED: Final[str] = (
    "persistence.decision_record.save_failed"
)
PERSISTENCE_DECISION_RECORD_QUERIED: Final[str] = "persistence.decision_record.queried"
PERSISTENCE_DECISION_RECORD_QUERY_FAILED: Final[str] = (
    "persistence.decision_record.query_failed"
)
PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED: Final[str] = (
    "persistence.decision_record.deserialize_failed"
)

PERSISTENCE_USER_SAVED: Final[str] = "persistence.user.saved"
PERSISTENCE_USER_SAVE_FAILED: Final[str] = "persistence.user.save_failed"
PERSISTENCE_USER_FETCHED: Final[str] = "persistence.user.fetched"
PERSISTENCE_USER_FETCH_FAILED: Final[str] = "persistence.user.fetch_failed"
PERSISTENCE_USER_LISTED: Final[str] = "persistence.user.listed"
PERSISTENCE_USER_LIST_FAILED: Final[str] = "persistence.user.list_failed"
PERSISTENCE_USER_COUNTED: Final[str] = "persistence.user.counted"
PERSISTENCE_USER_COUNT_FAILED: Final[str] = "persistence.user.count_failed"
PERSISTENCE_USER_COUNTED_BY_ROLE: Final[str] = "persistence.user.counted_by_role"
PERSISTENCE_USER_COUNT_BY_ROLE_FAILED: Final[str] = (
    "persistence.user.count_by_role_failed"
)
PERSISTENCE_USER_DELETED: Final[str] = "persistence.user.deleted"
PERSISTENCE_USER_DELETE_FAILED: Final[str] = "persistence.user.delete_failed"

PERSISTENCE_API_KEY_SAVED: Final[str] = "persistence.api_key.saved"
PERSISTENCE_API_KEY_SAVE_FAILED: Final[str] = "persistence.api_key.save_failed"
PERSISTENCE_API_KEY_FETCHED: Final[str] = "persistence.api_key.fetched"
PERSISTENCE_API_KEY_FETCH_FAILED: Final[str] = "persistence.api_key.fetch_failed"
PERSISTENCE_API_KEY_LISTED: Final[str] = "persistence.api_key.listed"
PERSISTENCE_API_KEY_LIST_FAILED: Final[str] = "persistence.api_key.list_failed"
PERSISTENCE_API_KEY_COUNT_FAILED: Final[str] = "persistence.api_key.count_failed"
PERSISTENCE_API_KEY_DELETED: Final[str] = "persistence.api_key.deleted"
PERSISTENCE_API_KEY_DELETE_FAILED: Final[str] = "persistence.api_key.delete_failed"

PERSISTENCE_SETTING_FETCHED: Final[str] = "persistence.setting.fetched"
PERSISTENCE_SETTING_FETCH_FAILED: Final[str] = "persistence.setting.fetch_failed"
PERSISTENCE_SETTING_SAVED: Final[str] = "persistence.setting.saved"
PERSISTENCE_SETTING_SAVE_FAILED: Final[str] = "persistence.setting.save_failed"

# Checkpoint events
PERSISTENCE_CHECKPOINT_SAVED: Final[str] = "persistence.checkpoint.saved"
PERSISTENCE_CHECKPOINT_SAVE_FAILED: Final[str] = "persistence.checkpoint.save_failed"
PERSISTENCE_CHECKPOINT_QUERIED: Final[str] = "persistence.checkpoint.queried"
PERSISTENCE_CHECKPOINT_QUERY_FAILED: Final[str] = "persistence.checkpoint.query_failed"
PERSISTENCE_CHECKPOINT_NOT_FOUND: Final[str] = "persistence.checkpoint.not_found"
PERSISTENCE_CHECKPOINT_DELETED: Final[str] = "persistence.checkpoint.deleted"
PERSISTENCE_CHECKPOINT_DELETE_FAILED: Final[str] = (
    "persistence.checkpoint.delete_failed"
)
PERSISTENCE_CHECKPOINT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.checkpoint.deserialize_failed"
)

# Heartbeat events
PERSISTENCE_HEARTBEAT_SAVED: Final[str] = "persistence.heartbeat.saved"
PERSISTENCE_HEARTBEAT_SAVE_FAILED: Final[str] = "persistence.heartbeat.save_failed"
PERSISTENCE_HEARTBEAT_QUERIED: Final[str] = "persistence.heartbeat.queried"
PERSISTENCE_HEARTBEAT_QUERY_FAILED: Final[str] = "persistence.heartbeat.query_failed"
PERSISTENCE_HEARTBEAT_NOT_FOUND: Final[str] = "persistence.heartbeat.not_found"
PERSISTENCE_HEARTBEAT_DELETED: Final[str] = "persistence.heartbeat.deleted"
PERSISTENCE_HEARTBEAT_DELETE_FAILED: Final[str] = "persistence.heartbeat.delete_failed"
PERSISTENCE_HEARTBEAT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.heartbeat.deserialize_failed"
)

# Agent state events
PERSISTENCE_AGENT_STATE_SAVED: Final[str] = "persistence.agent_state.saved"
PERSISTENCE_AGENT_STATE_SAVE_FAILED: Final[str] = "persistence.agent_state.save_failed"
PERSISTENCE_AGENT_STATE_FETCHED: Final[str] = "persistence.agent_state.fetched"
PERSISTENCE_AGENT_STATE_FETCH_FAILED: Final[str] = (
    "persistence.agent_state.fetch_failed"
)
PERSISTENCE_AGENT_STATE_NOT_FOUND: Final[str] = "persistence.agent_state.not_found"
PERSISTENCE_AGENT_STATE_ACTIVE_QUERIED: Final[str] = (
    "persistence.agent_state.active_queried"
)
PERSISTENCE_AGENT_STATE_ACTIVE_QUERY_FAILED: Final[str] = (
    "persistence.agent_state.active_query_failed"
)
PERSISTENCE_AGENT_STATE_LISTED: Final[str] = "persistence.agent_state.listed"
PERSISTENCE_AGENT_STATE_LIST_FAILED: Final[str] = "persistence.agent_state.list_failed"
PERSISTENCE_AGENT_STATE_DELETED: Final[str] = "persistence.agent_state.deleted"
PERSISTENCE_AGENT_STATE_DELETE_FAILED: Final[str] = (
    "persistence.agent_state.delete_failed"
)
PERSISTENCE_AGENT_STATE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.agent_state.deserialize_failed"
)

# Artifact events
PERSISTENCE_ARTIFACT_SAVED: Final[str] = "persistence.artifact.saved"
PERSISTENCE_ARTIFACT_SAVE_FAILED: Final[str] = "persistence.artifact.save_failed"
PERSISTENCE_ARTIFACT_FETCHED: Final[str] = "persistence.artifact.fetched"
PERSISTENCE_ARTIFACT_FETCH_FAILED: Final[str] = "persistence.artifact.fetch_failed"
PERSISTENCE_ARTIFACT_LISTED: Final[str] = "persistence.artifact.listed"
PERSISTENCE_ARTIFACT_LIST_FAILED: Final[str] = "persistence.artifact.list_failed"
PERSISTENCE_ARTIFACT_DELETED: Final[str] = "persistence.artifact.deleted"
PERSISTENCE_ARTIFACT_DELETE_FAILED: Final[str] = "persistence.artifact.delete_failed"
PERSISTENCE_ARTIFACT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.artifact.deserialize_failed"
)

# Artifact storage events
PERSISTENCE_ARTIFACT_STORED: Final[str] = "persistence.artifact_storage.stored"
PERSISTENCE_ARTIFACT_STORE_FAILED: Final[str] = (
    "persistence.artifact_storage.store_failed"
)
PERSISTENCE_ARTIFACT_RETRIEVED: Final[str] = "persistence.artifact_storage.retrieved"
PERSISTENCE_ARTIFACT_RETRIEVE_FAILED: Final[str] = (
    "persistence.artifact_storage.retrieve_failed"
)
PERSISTENCE_ARTIFACT_STORAGE_DELETED: Final[str] = (
    "persistence.artifact_storage.deleted"
)
PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED: Final[str] = (
    "persistence.artifact_storage.delete_failed"
)
PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED: Final[str] = (
    "persistence.artifact_storage.rollback_failed"
)
PERSISTENCE_ARTIFACT_CONTENT_MISSING: Final[str] = (
    "persistence.artifact_storage.content_missing"
)
PERSISTENCE_ARTIFACT_METADATA_MISSING: Final[str] = (
    "persistence.artifact.metadata_missing"
)
PERSISTENCE_ARTIFACT_DELETE_NO_STORAGE: Final[str] = (
    "persistence.artifact.delete_no_storage"
)

# Project events
PERSISTENCE_PROJECT_SAVED: Final[str] = "persistence.project.saved"
PERSISTENCE_PROJECT_SAVE_FAILED: Final[str] = "persistence.project.save_failed"
PERSISTENCE_PROJECT_FETCHED: Final[str] = "persistence.project.fetched"
PERSISTENCE_PROJECT_FETCH_FAILED: Final[str] = "persistence.project.fetch_failed"
PERSISTENCE_PROJECT_LISTED: Final[str] = "persistence.project.listed"
PERSISTENCE_PROJECT_LIST_FAILED: Final[str] = "persistence.project.list_failed"
PERSISTENCE_PROJECT_DELETED: Final[str] = "persistence.project.deleted"
PERSISTENCE_PROJECT_DELETE_FAILED: Final[str] = "persistence.project.delete_failed"
PERSISTENCE_PROJECT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project.deserialize_failed"
)

# Project workspace events
PERSISTENCE_PROJECT_WORKSPACE_SAVE_FAILED: Final[str] = (
    "persistence.project_workspace.save_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_FETCHED: Final[str] = (
    "persistence.project_workspace.fetched"
)
PERSISTENCE_PROJECT_WORKSPACE_FETCH_FAILED: Final[str] = (
    "persistence.project_workspace.fetch_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_LISTED: Final[str] = (
    "persistence.project_workspace.listed"
)
PERSISTENCE_PROJECT_WORKSPACE_LIST_FAILED: Final[str] = (
    "persistence.project_workspace.list_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED: Final[str] = (
    "persistence.project_workspace.delete_failed"
)
PERSISTENCE_PROJECT_WORKSPACE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_workspace.deserialize_failed"
)

# Project environment events
PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED: Final[str] = (
    "persistence.project_environment.save_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_FETCHED: Final[str] = (
    "persistence.project_environment.fetched"
)
PERSISTENCE_PROJECT_ENVIRONMENT_FETCH_FAILED: Final[str] = (
    "persistence.project_environment.fetch_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_LISTED: Final[str] = (
    "persistence.project_environment.listed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED: Final[str] = (
    "persistence.project_environment.list_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED: Final[str] = (
    "persistence.project_environment.delete_failed"
)
PERSISTENCE_PROJECT_ENVIRONMENT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_environment.deserialize_failed"
)

# Living-documentation metadata events
PERSISTENCE_PROJECT_DOC_SAVE_FAILED: Final[str] = "persistence.project_doc.save_failed"
PERSISTENCE_PROJECT_DOC_FETCHED: Final[str] = "persistence.project_doc.fetched"
PERSISTENCE_PROJECT_DOC_FETCH_FAILED: Final[str] = (
    "persistence.project_doc.fetch_failed"
)
PERSISTENCE_PROJECT_DOC_LISTED: Final[str] = "persistence.project_doc.listed"
PERSISTENCE_PROJECT_DOC_LIST_FAILED: Final[str] = "persistence.project_doc.list_failed"
PERSISTENCE_PROJECT_DOC_QUERIED: Final[str] = "persistence.project_doc.queried"
PERSISTENCE_PROJECT_DOC_QUERY_FAILED: Final[str] = (
    "persistence.project_doc.query_failed"
)
PERSISTENCE_PROJECT_DOC_COUNTED: Final[str] = "persistence.project_doc.counted"
PERSISTENCE_PROJECT_DOC_COUNT_FAILED: Final[str] = (
    "persistence.project_doc.count_failed"
)
PERSISTENCE_PROJECT_DOC_DELETE_FAILED: Final[str] = (
    "persistence.project_doc.delete_failed"
)
PERSISTENCE_PROJECT_DOC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_doc.deserialize_failed"
)

# -- Knowledge source registry events -----------------------------------------

PERSISTENCE_KNOWLEDGE_SOURCE_SAVE_FAILED: Final[str] = (
    "persistence.knowledge_source.save_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_FETCHED: Final[str] = (
    "persistence.knowledge_source.fetched"
)
PERSISTENCE_KNOWLEDGE_SOURCE_FETCH_FAILED: Final[str] = (
    "persistence.knowledge_source.fetch_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_LISTED: Final[str] = "persistence.knowledge_source.listed"
PERSISTENCE_KNOWLEDGE_SOURCE_LIST_FAILED: Final[str] = (
    "persistence.knowledge_source.list_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_QUERIED: Final[str] = (
    "persistence.knowledge_source.queried"
)
PERSISTENCE_KNOWLEDGE_SOURCE_QUERY_FAILED: Final[str] = (
    "persistence.knowledge_source.query_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_COUNTED: Final[str] = (
    "persistence.knowledge_source.counted"
)
PERSISTENCE_KNOWLEDGE_SOURCE_COUNT_FAILED: Final[str] = (
    "persistence.knowledge_source.count_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED: Final[str] = (
    "persistence.knowledge_source.delete_failed"
)
PERSISTENCE_KNOWLEDGE_SOURCE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.knowledge_source.deserialize_failed"
)

# -- Knowledge chunk provenance events ----------------------------------------

PERSISTENCE_KNOWLEDGE_PROVENANCE_SAVE_FAILED: Final[str] = (
    "persistence.knowledge_provenance.save_failed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCHED: Final[str] = (
    "persistence.knowledge_provenance.fetched"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCH_FAILED: Final[str] = (
    "persistence.knowledge_provenance.fetch_failed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_LISTED: Final[str] = (
    "persistence.knowledge_provenance.listed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_LIST_FAILED: Final[str] = (
    "persistence.knowledge_provenance.list_failed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERIED: Final[str] = (
    "persistence.knowledge_provenance.queried"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERY_FAILED: Final[str] = (
    "persistence.knowledge_provenance.query_failed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_COUNTED: Final[str] = (
    "persistence.knowledge_provenance.counted"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_COUNT_FAILED: Final[str] = (
    "persistence.knowledge_provenance.count_failed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_DELETE_FAILED: Final[str] = (
    "persistence.knowledge_provenance.delete_failed"
)
PERSISTENCE_KNOWLEDGE_PROVENANCE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.knowledge_provenance.deserialize_failed"
)

# -- Project cost aggregate events --------------------------------------------

PERSISTENCE_PROJECT_COST_AGG_INCREMENTED: Final[str] = (
    "persistence.project_cost_agg.incremented"
)
PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED: Final[str] = (
    "persistence.project_cost_agg.increment_failed"
)
PERSISTENCE_PROJECT_COST_AGG_FETCHED: Final[str] = (
    "persistence.project_cost_agg.fetched"
)
PERSISTENCE_PROJECT_COST_AGG_FETCH_FAILED: Final[str] = (
    "persistence.project_cost_agg.fetch_failed"
)
PERSISTENCE_PROJECT_COST_AGG_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_cost_agg.deserialize_failed"
)
PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING: Final[str] = (
    "persistence.project_cost_agg.currency_pin_missing"
)

# -- Workflow definition events -----------------------------------------------

PERSISTENCE_WORKFLOW_DEF_SAVED: Final[str] = "persistence.workflow_def.saved"
PERSISTENCE_WORKFLOW_DEF_SAVE_FAILED: Final[str] = (
    "persistence.workflow_def.save_failed"
)
PERSISTENCE_WORKFLOW_DEF_FETCHED: Final[str] = "persistence.workflow_def.fetched"
PERSISTENCE_WORKFLOW_DEF_FETCH_FAILED: Final[str] = (
    "persistence.workflow_def.fetch_failed"
)
PERSISTENCE_WORKFLOW_DEF_LISTED: Final[str] = "persistence.workflow_def.listed"
PERSISTENCE_WORKFLOW_DEF_LIST_FAILED: Final[str] = (
    "persistence.workflow_def.list_failed"
)
PERSISTENCE_WORKFLOW_DEF_DELETED: Final[str] = "persistence.workflow_def.deleted"
PERSISTENCE_WORKFLOW_DEF_DELETE_FAILED: Final[str] = (
    "persistence.workflow_def.delete_failed"
)
PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED: Final[str] = (
    "persistence.workflow_def.deserialize_failed"
)

# -- Subworkflow registry events ---------------------------------------------

PERSISTENCE_SUBWORKFLOW_SAVED: Final[str] = "persistence.subworkflow.saved"
PERSISTENCE_SUBWORKFLOW_SAVE_FAILED: Final[str] = "persistence.subworkflow.save_failed"
PERSISTENCE_SUBWORKFLOW_FETCHED: Final[str] = "persistence.subworkflow.fetched"
PERSISTENCE_SUBWORKFLOW_FETCH_FAILED: Final[str] = (
    "persistence.subworkflow.fetch_failed"
)
PERSISTENCE_SUBWORKFLOW_LISTED: Final[str] = "persistence.subworkflow.listed"
PERSISTENCE_SUBWORKFLOW_LIST_FAILED: Final[str] = "persistence.subworkflow.list_failed"
PERSISTENCE_SUBWORKFLOW_DELETED: Final[str] = "persistence.subworkflow.deleted"
PERSISTENCE_SUBWORKFLOW_DELETE_FAILED: Final[str] = (
    "persistence.subworkflow.delete_failed"
)
PERSISTENCE_SUBWORKFLOW_DESERIALIZE_FAILED: Final[str] = (
    "persistence.subworkflow.deserialize_failed"
)

# -- Workflow execution events -----------------------------------------------

PERSISTENCE_WORKFLOW_EXEC_SAVED: Final[str] = "persistence.workflow_exec.saved"
PERSISTENCE_WORKFLOW_EXEC_SAVE_FAILED: Final[str] = (
    "persistence.workflow_exec.save_failed"
)
PERSISTENCE_WORKFLOW_EXEC_FETCHED: Final[str] = "persistence.workflow_exec.fetched"
PERSISTENCE_WORKFLOW_EXEC_FETCH_FAILED: Final[str] = (
    "persistence.workflow_exec.fetch_failed"
)
PERSISTENCE_WORKFLOW_EXEC_LISTED: Final[str] = "persistence.workflow_exec.listed"
PERSISTENCE_WORKFLOW_EXEC_LIST_FAILED: Final[str] = (
    "persistence.workflow_exec.list_failed"
)
PERSISTENCE_WORKFLOW_EXEC_DELETED: Final[str] = "persistence.workflow_exec.deleted"
PERSISTENCE_WORKFLOW_EXEC_DELETE_FAILED: Final[str] = (
    "persistence.workflow_exec.delete_failed"
)
PERSISTENCE_WORKFLOW_EXEC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.workflow_exec.deserialize_failed"
)
PERSISTENCE_WORKFLOW_EXEC_FOUND_BY_TASK: Final[str] = (
    "persistence.workflow_exec.found_by_task"
)
PERSISTENCE_WORKFLOW_EXEC_FIND_BY_TASK_FAILED: Final[str] = (
    "persistence.workflow_exec.find_by_task_failed"
)

# -- Risk override events ---------------------------------------------------

PERSISTENCE_RISK_OVERRIDE_SAVED: Final[str] = "persistence.risk_override.saved"
PERSISTENCE_RISK_OVERRIDE_SAVE_FAILED: Final[str] = (
    "persistence.risk_override.save_failed"
)
PERSISTENCE_RISK_OVERRIDE_REVOKE_FAILED: Final[str] = (
    "persistence.risk_override.revoke_failed"
)
PERSISTENCE_RISK_OVERRIDE_DELETE_FAILED: Final[str] = (
    "persistence.risk_override.delete_failed"
)
PERSISTENCE_RISK_OVERRIDE_QUERIED: Final[str] = "persistence.risk_override.queried"
PERSISTENCE_RISK_OVERRIDE_QUERY_FAILED: Final[str] = (
    "persistence.risk_override.query_failed"
)

# -- Preset override events -------------------------------------------------

PERSISTENCE_PRESET_OVERRIDE_SAVE_FAILED: Final[str] = (
    "persistence.preset_override.save_failed"
)
PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED: Final[str] = (
    "persistence.preset_override.query_failed"
)
PERSISTENCE_PRESET_OVERRIDE_DELETE_FAILED: Final[str] = (
    "persistence.preset_override.delete_failed"
)

# -- SSRF violation events --------------------------------------------------

PERSISTENCE_SSRF_VIOLATION_SAVED: Final[str] = "persistence.ssrf_violation.saved"
PERSISTENCE_SSRF_VIOLATION_SAVE_FAILED: Final[str] = (
    "persistence.ssrf_violation.save_failed"
)
PERSISTENCE_SSRF_VIOLATION_STATUS_UPDATED: Final[str] = (
    "persistence.ssrf_violation.status_updated"
)
PERSISTENCE_SSRF_VIOLATION_QUERIED: Final[str] = "persistence.ssrf_violation.queried"
PERSISTENCE_SSRF_VIOLATION_QUERY_FAILED: Final[str] = (
    "persistence.ssrf_violation.query_failed"
)

# Connection events (durable connection catalog)
PERSISTENCE_CONNECTION_SAVED: Final[str] = "persistence.connection.saved"
PERSISTENCE_CONNECTION_SAVE_FAILED: Final[str] = "persistence.connection.save_failed"
PERSISTENCE_CONNECTION_FETCHED: Final[str] = "persistence.connection.fetched"
PERSISTENCE_CONNECTION_FETCH_FAILED: Final[str] = "persistence.connection.fetch_failed"
PERSISTENCE_CONNECTION_LISTED: Final[str] = "persistence.connection.listed"
PERSISTENCE_CONNECTION_LIST_FAILED: Final[str] = "persistence.connection.list_failed"
PERSISTENCE_CONNECTION_DELETED: Final[str] = "persistence.connection.deleted"
PERSISTENCE_CONNECTION_DELETE_FAILED: Final[str] = (
    "persistence.connection.delete_failed"
)
PERSISTENCE_CONNECTION_DESERIALIZE_FAILED: Final[str] = (
    "persistence.connection.deserialize_failed"
)

# Connection secret events (encrypted blob storage for SecretBackend).
# ``noqa: S105`` -- the ``_SECRET_`` token in the constant *name* is an
# observability domain, not a hardcoded credential value. The S105 check
# fires on the assigned string literal because the identifier matches a
# secret-like pattern; suppressed once per literal.
PERSISTENCE_CONNECTION_SECRET_STORED: Final[str] = (
    "persistence.connection_secret.stored"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_STORE_FAILED: Final[str] = (
    "persistence.connection_secret.store_failed"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_RETRIEVED: Final[str] = (
    "persistence.connection_secret.retrieved"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_RETRIEVE_FAILED: Final[str] = (
    "persistence.connection_secret.retrieve_failed"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_DELETED: Final[str] = (
    "persistence.connection_secret.deleted"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_DELETE_FAILED: Final[str] = (
    "persistence.connection_secret.delete_failed"  # noqa: S105
)

# OAuth state events (transient authorization-flow state)
PERSISTENCE_OAUTH_STATE_SAVED: Final[str] = "persistence.oauth_state.saved"
PERSISTENCE_OAUTH_STATE_SAVE_FAILED: Final[str] = "persistence.oauth_state.save_failed"
PERSISTENCE_OAUTH_STATE_FETCHED: Final[str] = "persistence.oauth_state.fetched"
PERSISTENCE_OAUTH_STATE_FETCH_FAILED: Final[str] = (
    "persistence.oauth_state.fetch_failed"
)
PERSISTENCE_OAUTH_STATE_DELETED: Final[str] = "persistence.oauth_state.deleted"
PERSISTENCE_OAUTH_STATE_DELETE_FAILED: Final[str] = (
    "persistence.oauth_state.delete_failed"
)
PERSISTENCE_OAUTH_STATE_CLEANUP: Final[str] = "persistence.oauth_state.cleanup"
PERSISTENCE_OAUTH_STATE_CLEANUP_FAILED: Final[str] = (
    "persistence.oauth_state.cleanup_failed"
)
PERSISTENCE_OAUTH_STATE_DESERIALIZE_FAILED: Final[str] = (
    "persistence.oauth_state.deserialize_failed"
)

# Webhook receipt events (provider event log)
PERSISTENCE_WEBHOOK_RECEIPT_LOGGED: Final[str] = "persistence.webhook_receipt.logged"
PERSISTENCE_WEBHOOK_RECEIPT_LOG_FAILED: Final[str] = (
    "persistence.webhook_receipt.log_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_LISTED: Final[str] = "persistence.webhook_receipt.listed"
PERSISTENCE_WEBHOOK_RECEIPT_LIST_FAILED: Final[str] = (
    "persistence.webhook_receipt.list_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_DELETE_FAILED: Final[str] = (
    "persistence.webhook_receipt.delete_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP: Final[str] = "persistence.webhook_receipt.cleanup"
PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED: Final[str] = (
    "persistence.webhook_receipt.cleanup_failed"
)
PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_PAUSED: Final[str] = (
    "persistence.webhook_receipt.cleanup_paused"
)
PERSISTENCE_WEBHOOK_RECEIPT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.webhook_receipt.deserialize_failed"
)

# Circuit breaker state events
PERSISTENCE_CIRCUIT_BREAKER_SAVED: Final[str] = "persistence.circuit_breaker.saved"
PERSISTENCE_CIRCUIT_BREAKER_SAVE_FAILED: Final[str] = (
    "persistence.circuit_breaker.save_failed"
)
PERSISTENCE_CIRCUIT_BREAKER_LOADED: Final[str] = "persistence.circuit_breaker.loaded"
PERSISTENCE_CIRCUIT_BREAKER_LOAD_FAILED: Final[str] = (
    "persistence.circuit_breaker.load_failed"
)
PERSISTENCE_CIRCUIT_BREAKER_DELETED: Final[str] = "persistence.circuit_breaker.deleted"
PERSISTENCE_CIRCUIT_BREAKER_DELETE_FAILED: Final[str] = (
    "persistence.circuit_breaker.delete_failed"
)

# Ceremony scheduler state events (per-sprint snapshot persistence)
PERSISTENCE_CEREMONY_STATE_SAVED: Final[str] = "persistence.ceremony_state.saved"
PERSISTENCE_CEREMONY_STATE_SAVE_FAILED: Final[str] = (
    "persistence.ceremony_state.save_failed"
)
PERSISTENCE_CEREMONY_STATE_LOADED: Final[str] = "persistence.ceremony_state.loaded"
PERSISTENCE_CEREMONY_STATE_LOAD_FAILED: Final[str] = (
    "persistence.ceremony_state.load_failed"
)
PERSISTENCE_CEREMONY_STATE_DELETED: Final[str] = "persistence.ceremony_state.deleted"
PERSISTENCE_CEREMONY_STATE_DELETE_FAILED: Final[str] = (
    "persistence.ceremony_state.delete_failed"
)

# Meeting cooldown events (per-meeting-type last-triggered persistence)
PERSISTENCE_MEETING_COOLDOWN_UPSERTED: Final[str] = (
    "persistence.meeting_cooldown.upserted"
)
PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED: Final[str] = (
    "persistence.meeting_cooldown.upsert_failed"
)
PERSISTENCE_MEETING_COOLDOWN_LOADED: Final[str] = "persistence.meeting_cooldown.loaded"
PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED: Final[str] = (
    "persistence.meeting_cooldown.load_failed"
)
PERSISTENCE_MEETING_COOLDOWN_DELETED: Final[str] = (
    "persistence.meeting_cooldown.deleted"
)
PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED: Final[str] = (
    "persistence.meeting_cooldown.delete_failed"
)

# Tracked Docker container events (sandbox lifecycle persistence)
PERSISTENCE_TRACKED_CONTAINER_SAVED: Final[str] = "persistence.tracked_container.saved"
PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED: Final[str] = (
    "persistence.tracked_container.save_failed"
)
PERSISTENCE_TRACKED_CONTAINER_LOADED: Final[str] = (
    "persistence.tracked_container.loaded"
)
PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED: Final[str] = (
    "persistence.tracked_container.load_failed"
)
PERSISTENCE_TRACKED_CONTAINER_DELETED: Final[str] = (
    "persistence.tracked_container.deleted"
)
PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED: Final[str] = (
    "persistence.tracked_container.delete_failed"
)

# Worker seen-claims events (TaskClaim idempotency dedup)
PERSISTENCE_SEEN_CLAIMS_MARK_FAILED: Final[str] = "persistence.seen_claims.mark_failed"
PERSISTENCE_SEEN_CLAIMS_LOOKUP_FAILED: Final[str] = (
    "persistence.seen_claims.lookup_failed"
)
PERSISTENCE_SEEN_CLAIMS_PRUNED: Final[str] = "persistence.seen_claims.pruned"
PERSISTENCE_SEEN_CLAIMS_PRUNE_FAILED: Final[str] = (
    "persistence.seen_claims.prune_failed"
)

# Cost-tracker claim-dedup events (persistent claim_id dedup)
PERSISTENCE_COST_CLAIM_DEDUPE_FAILED: Final[str] = (
    "persistence.cost_claim_dedupe.claim_failed"
)
PERSISTENCE_COST_CLAIM_DEDUPE_PRUNED: Final[str] = (
    "persistence.cost_claim_dedupe.pruned"
)
PERSISTENCE_COST_CLAIM_DEDUPE_PRUNE_FAILED: Final[str] = (
    "persistence.cost_claim_dedupe.prune_failed"
)

# Principle-override repo events (rollback PromptMutator backing store)
PERSISTENCE_PRINCIPLE_OVERRIDE_SAVE_FAILED: Final[str] = (
    "persistence.principle_override.save_failed"
)
PERSISTENCE_PRINCIPLE_OVERRIDE_GET_FAILED: Final[str] = (
    "persistence.principle_override.get_failed"
)
PERSISTENCE_PRINCIPLE_OVERRIDE_DELETE_FAILED: Final[str] = (
    "persistence.principle_override.delete_failed"
)
PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED: Final[str] = (
    "persistence.principle_override.list_failed"
)

# Conversational clarify-and-propose repo events. Failure paths only:
# the persistence-boundary gate forbids repos from emitting their own
# mutation lifecycle (_SAVED / _DELETED) events; the service layer
# owns the audit hop. FETCHED / LISTED / QUERIED are debug-level
# read markers and APPENDED is an immutable-log marker, none of which
# the gate considers mutations.
PERSISTENCE_CONVERSATION_FETCHED: Final[str] = "persistence.conversation.fetched"
PERSISTENCE_CONVERSATION_LISTED: Final[str] = "persistence.conversation.listed"
PERSISTENCE_CONVERSATION_FAILED: Final[str] = "persistence.conversation.failed"
PERSISTENCE_CONVERSATION_TURN_APPENDED: Final[str] = (
    "persistence.conversation_turn.appended"
)
PERSISTENCE_CONVERSATION_TURN_QUERIED: Final[str] = (
    "persistence.conversation_turn.queried"
)
PERSISTENCE_CONVERSATION_TURN_FAILED: Final[str] = (
    "persistence.conversation_turn.failed"
)
PERSISTENCE_CONVERSATIONAL_PROPOSAL_FETCHED: Final[str] = (
    "persistence.conversational_proposal.fetched"
)
PERSISTENCE_CONVERSATIONAL_PROPOSAL_LISTED: Final[str] = (
    "persistence.conversational_proposal.listed"
)
PERSISTENCE_CONVERSATIONAL_PROPOSAL_FAILED: Final[str] = (
    "persistence.conversational_proposal.failed"
)
PERSISTENCE_CONVERSATIONAL_UNKNOWN_BACKEND: Final[str] = (
    "persistence.conversational.unknown_backend"
)
PERSISTENCE_CONVERSATIONAL_HANDLE_UNAVAILABLE: Final[str] = (
    "persistence.conversational.handle_unavailable"
)

# Dynamic tool blueprint events (self-extending toolkit). Failure paths
# plus read/query markers only: the persistence-boundary gate forbids
# repos from emitting their own mutation lifecycle (_SAVED / _DELETED)
# events; the toolsmith service layer owns the audit hop.
PERSISTENCE_DYNAMIC_TOOL_FETCHED: Final[str] = "persistence.dynamic_tool.fetched"
PERSISTENCE_DYNAMIC_TOOL_FETCH_FAILED: Final[str] = (
    "persistence.dynamic_tool.fetch_failed"
)
PERSISTENCE_DYNAMIC_TOOL_LISTED: Final[str] = "persistence.dynamic_tool.listed"
PERSISTENCE_DYNAMIC_TOOL_LIST_FAILED: Final[str] = (
    "persistence.dynamic_tool.list_failed"
)
PERSISTENCE_DYNAMIC_TOOL_QUERIED: Final[str] = "persistence.dynamic_tool.queried"
PERSISTENCE_DYNAMIC_TOOL_QUERY_FAILED: Final[str] = (
    "persistence.dynamic_tool.query_failed"
)
PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED: Final[str] = (
    "persistence.dynamic_tool.save_failed"
)
PERSISTENCE_DYNAMIC_TOOL_DELETE_FAILED: Final[str] = (
    "persistence.dynamic_tool.delete_failed"
)
PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED: Final[str] = (
    "persistence.dynamic_tool.transition_failed"
)
PERSISTENCE_DYNAMIC_TOOL_DESERIALIZE_FAILED: Final[str] = (
    "persistence.dynamic_tool.deserialize_failed"
)

# -- Research run events -------------------------------------------------------

PERSISTENCE_RESEARCH_RUN_SAVE_FAILED: Final[str] = (
    "persistence.research_run.save_failed"
)
PERSISTENCE_RESEARCH_RUN_FETCHED: Final[str] = "persistence.research_run.fetched"
PERSISTENCE_RESEARCH_RUN_FETCH_FAILED: Final[str] = (
    "persistence.research_run.fetch_failed"
)
PERSISTENCE_RESEARCH_RUN_LISTED: Final[str] = "persistence.research_run.listed"
PERSISTENCE_RESEARCH_RUN_LIST_FAILED: Final[str] = (
    "persistence.research_run.list_failed"
)
PERSISTENCE_RESEARCH_RUN_QUERIED: Final[str] = "persistence.research_run.queried"
PERSISTENCE_RESEARCH_RUN_QUERY_FAILED: Final[str] = (
    "persistence.research_run.query_failed"
)
PERSISTENCE_RESEARCH_RUN_COUNTED: Final[str] = "persistence.research_run.counted"
PERSISTENCE_RESEARCH_RUN_COUNT_FAILED: Final[str] = (
    "persistence.research_run.count_failed"
)
PERSISTENCE_RESEARCH_RUN_DELETE_FAILED: Final[str] = (
    "persistence.research_run.delete_failed"
)
PERSISTENCE_RESEARCH_RUN_DESERIALIZE_FAILED: Final[str] = (
    "persistence.research_run.deserialize_failed"
)
