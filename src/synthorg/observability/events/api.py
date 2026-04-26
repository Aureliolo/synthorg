"""API event constants."""

from typing import Final

API_REQUEST_STARTED: Final[str] = "api.request.started"
API_STATE_SERVICE_ATTACHED: Final[str] = "api.state.service_attached"
API_REQUEST_COMPLETED: Final[str] = "api.request.completed"
API_REQUEST_ERROR: Final[str] = "api.request.error"
API_HEALTH_CHECK: Final[str] = "api.health.check"
API_APP_STARTUP: Final[str] = "api.app.startup"
API_APP_SHUTDOWN: Final[str] = "api.app.shutdown"
API_WS_CONNECTED: Final[str] = "api.ws.connected"
API_WS_DISCONNECTED: Final[str] = "api.ws.disconnected"
API_AUTH_USER_FALLBACK: Final[str] = "api.auth.user_fallback"
API_GUARD_DENIED: Final[str] = "api.guard.denied"
API_GUARD_DEGRADED_AUTH: Final[str] = "api.guard.degraded_auth"
API_BUS_BRIDGE_SUBSCRIBE_FAILED: Final[str] = "api.bus_bridge.subscribe.failed"
API_BUS_BRIDGE_POLL_ERROR: Final[str] = "api.bus_bridge.poll.error"
API_WS_INVALID_MESSAGE: Final[str] = "api.ws.invalid_message"
API_WS_SUBSCRIBE: Final[str] = "api.ws.subscribe"
API_WS_UNSUBSCRIBE: Final[str] = "api.ws.unsubscribe"
API_WS_UNKNOWN_ACTION: Final[str] = "api.ws.unknown_action"
API_RESOURCE_NOT_FOUND: Final[str] = "api.resource.not_found"
API_TASK_UPDATED: Final[str] = "api.task.updated"
API_TASK_DELETED: Final[str] = "api.task.deleted"
API_TASK_CANCELLED: Final[str] = "api.task.cancelled"
API_APPROVAL_CREATED: Final[str] = "api.approval.created"
# api.approval.approved / api.approval.rejected moved to events.security
# (SECURITY_APPROVAL_APPROVED / SECURITY_APPROVAL_REJECTED) so the audit
# chain signs the human decision.
API_APPROVAL_EXPIRED: Final[str] = "api.approval.expired"
API_APPROVAL_EXPIRE_CALLBACK_FAILED: Final[str] = "api.approval.expire_callback_failed"
API_APPROVAL_PUBLISH_FAILED: Final[str] = "api.approval.publish_failed"
API_APPROVAL_CONFLICT: Final[str] = "api.approval.conflict"
API_APPROVAL_STORE_CLEARED: Final[str] = "api.approval.store_cleared"
API_APPROVAL_REPO_SAVED: Final[str] = "api.approval.repo_saved"
API_APPROVAL_REPO_FETCHED: Final[str] = "api.approval.repo_fetched"
API_APPROVAL_REPO_LISTED: Final[str] = "api.approval.repo_listed"
API_APPROVAL_REPO_DELETED: Final[str] = "api.approval.repo_deleted"
API_APPROVAL_REPO_FAILED: Final[str] = "api.approval.repo_failed"
API_BRIDGE_CHANNEL_DEAD: Final[str] = "api.bus_bridge.channel_dead"
API_WS_TRANSPORT_ERROR: Final[str] = "api.ws.transport_error"
API_WS_SEND_FAILED: Final[str] = "api.ws.send_failed"
API_SERVICE_UNAVAILABLE: Final[str] = "api.service.unavailable"
API_SERVICE_AUTO_WIRED: Final[str] = "api.service.auto_wired"
API_SERVICE_AUTO_WIRE_FAILED: Final[str] = "api.service.auto_wire_failed"
# api.auth.success / failed / token_issued / setup_complete / password_changed
# moved to events.security as SECURITY_AUTH_* (audit-chained).
API_AUTH_GUARD_SKIPPED: Final[str] = "api.auth.guard_skipped"
API_TASK_TRANSITION_FAILED: Final[str] = "api.task.transition_failed"
API_TASK_MUTATION_FAILED: Final[str] = "api.task.mutation_failed"
API_TASK_CREATED_BY_MISMATCH: Final[str] = "api.task.created_by_mismatch"
API_AUTH_SYSTEM_USER_ENSURED: Final[str] = "api.auth.system_user_ensured"
API_AUTH_FALLBACK: Final[str] = "api.auth.fallback"
API_AUTH_CONFIG_FALLBACK: Final[str] = "api.auth.config_fallback"
API_AUTH_COOKIE_NAME_FALLBACK: Final[str] = "api.auth.cookie_name_fallback"
API_MEMORY_DIR_TMPROOT_FALLBACK: Final[str] = "api.memory_dir.tmproot_fallback"
API_ROUTE_NOT_FOUND: Final[str] = "api.route.not_found"
API_COORDINATION_STARTED: Final[str] = "api.coordination.started"
API_COORDINATION_COMPLETED: Final[str] = "api.coordination.completed"
API_COORDINATION_FAILED: Final[str] = "api.coordination.failed"
API_COORDINATION_AGENT_RESOLVE_FAILED: Final[str] = (
    "api.coordination.agent_resolve_failed"
)
API_CONTENT_NEGOTIATED: Final[str] = "api.content.negotiated"
API_CORRELATION_FALLBACK: Final[str] = "api.correlation.fallback"
API_ACCEPT_PARSE_FAILED: Final[str] = "api.accept.parse_failed"
API_WS_TICKET_ISSUED: Final[str] = "api.ws.ticket_issued"
API_WS_TICKET_CONSUMED: Final[str] = "api.ws.ticket_consumed"
API_WS_TICKET_EXPIRED: Final[str] = "api.ws.ticket_expired"
API_WS_TICKET_INVALID: Final[str] = "api.ws.ticket_invalid"
API_WS_TICKET_CLEANUP: Final[str] = "api.ws.ticket_cleanup"
API_AUDIT_RETENTION: Final[str] = "api.audit.retention"
API_WS_AUTH_STAGE: Final[str] = "api.ws.auth_stage"
API_WS_AUTH_OK: Final[str] = "api.ws.auth_ok"
API_WS_PING: Final[str] = "api.ws.ping"
API_WS_EVENT_DROPPED: Final[str] = "api.ws.event_dropped"
API_WS_BACKPRESSURE_DROPPED: Final[str] = "api.ws.backpressure_dropped"

# SSE streaming
API_SSE_PULL_MODEL_FAILED: Final[str] = "api.sse.pull_model_failed"
API_MODEL_OPERATION_FAILED: Final[str] = "api.model.operation_failed"
API_OPENAPI_SCHEMA_ENHANCED: Final[str] = "api.openapi.schema_enhanced"
API_RESOURCE_CONFLICT: Final[str] = "api.resource.conflict"
API_VALIDATION_FAILED: Final[str] = "api.validation.failed"
API_ASGI_MISSING_STATUS: Final[str] = "api.asgi.missing_status"
API_AGENT_PERFORMANCE_QUERIED: Final[str] = "api.agent.performance_queried"
API_AGENT_ACTIVITY_QUERIED: Final[str] = "api.agent.activity_queried"
API_AGENT_HISTORY_QUERIED: Final[str] = "api.agent.history_queried"
API_DEPARTMENT_HEALTH_QUERIED: Final[str] = "api.department.health_queried"
API_PROVIDER_HEALTH_QUERIED: Final[str] = "api.provider.health_queried"
API_PROVIDER_USAGE_ENRICHMENT_FAILED: Final[str] = (
    "api.provider.usage_enrichment_failed"
)
API_ACTIVITY_FEED_QUERIED: Final[str] = "api.activity.feed_queried"
API_MEETING_TRIGGERED: Final[str] = "api.meeting.triggered"
API_BUDGET_RECORDS_LISTED: Final[str] = "api.budget.records_listed"
# api.user.created / updated / deleted moved to events.security as
# SECURITY_USER_* (audit-chained); listing + save-failure stay here.
API_USER_SAVE_FAILED: Final[str] = "api.user.save_failed"
API_USER_LISTED: Final[str] = "api.user.listed"

# Session management
# api.session.created / revoked / force_logout / limit_enforced moved
# to events.security as SECURITY_SESSION_* (audit-chained).
API_SESSION_CREATE_FAILED: Final[str] = "api.session.create_failed"
API_SESSION_LISTED: Final[str] = "api.session.listed"
API_SESSION_CLEANUP: Final[str] = "api.session.cleanup"
API_SESSION_REVOKE_FAILED: Final[str] = "api.session.revoke_failed"

# CSRF
# api.csrf.rejected moved to events.security as SECURITY_CSRF_REJECTED.
API_CSRF_SKIPPED: Final[str] = "api.csrf.skipped"

# Account lockout
# api.auth.account_locked / lockout_cleared moved to events.security as
# SECURITY_AUTH_ACCOUNT_LOCKED / SECURITY_AUTH_LOCKOUT_CLEARED.
API_AUTH_LOCKOUT_CLEANUP: Final[str] = "api.auth.lockout_cleanup"

# Refresh tokens
# api.auth.refresh_created / consumed / rejected / revoked moved to
# events.security as SECURITY_AUTH_REFRESH_* (audit-chained).
API_AUTH_REFRESH_CLEANUP: Final[str] = "api.auth.refresh_cleanup"

# Cookie auth
API_AUTH_COOKIE_USED: Final[str] = "api.auth.cookie_used"

# Network exposure
API_TLS_CONFIGURED: Final[str] = "api.tls.configured"
API_NETWORK_EXPOSURE_WARNING: Final[str] = "api.network.exposure_warning"

# Concurrent access
API_CONCURRENCY_CONFLICT: Final[str] = "api.concurrency.conflict"

# WebSocket user channels
API_WS_USER_CHANNEL_DENIED: Final[str] = "api.ws.user_channel_denied"

# Control-plane query endpoints
API_AUDIT_QUERIED: Final[str] = "api.audit.queried"
API_AGENT_HEALTH_QUERIED: Final[str] = "api.agent.health_queried"
API_SECURITY_CONFIG_EXPORTED: Final[str] = "api.security_config.exported"
API_SECURITY_CONFIG_IMPORTED: Final[str] = "api.security_config.imported"
API_SECURITY_CONFIG_IMPORT_FAILED: Final[str] = "api.security_config.import_failed"
API_COORDINATION_METRICS_QUERIED: Final[str] = "api.coordination_metrics.queried"
API_AGENT_HEALTH_TREND_MISSING: Final[str] = "api.agent.health.trend_missing"

# Ceremony policy
API_CEREMONY_POLICY_QUERIED: Final[str] = "api.ceremony_policy.queried"
API_CEREMONY_POLICY_RESOLVED: Final[str] = "api.ceremony_policy.resolved"
API_CEREMONY_POLICY_ACTIVE_QUERIED: Final[str] = "api.ceremony_policy.active_queried"
API_CEREMONY_POLICY_DEPT_UPDATED: Final[str] = "api.ceremony_policy.department_updated"
API_CEREMONY_POLICY_DEPT_CLEARED: Final[str] = "api.ceremony_policy.department_cleared"

# Team CRUD
API_TEAM_CREATED: Final[str] = "api.team.created"
API_TEAM_UPDATED: Final[str] = "api.team.updated"
API_TEAM_DELETED: Final[str] = "api.team.deleted"
API_TEAM_REORDERED: Final[str] = "api.team.reordered"

# Budget validation
API_BUDGET_REBALANCE_APPLIED: Final[str] = "api.budget.rebalance_applied"
API_BUDGET_VALIDATION_FAILED: Final[str] = "api.budget.validation_failed"

# Company mutations
API_COMPANY_UPDATED: Final[str] = "api.company.updated"

# Department mutations
API_DEPARTMENT_CREATED: Final[str] = "api.department.created"
API_DEPARTMENT_UPDATED: Final[str] = "api.department.updated"
API_DEPARTMENT_DELETED: Final[str] = "api.department.deleted"
API_DEPARTMENTS_REORDERED: Final[str] = "api.departments.reordered"

# Agent mutations
API_AGENT_CREATED: Final[str] = "api.agent.created"
API_AGENT_UPDATED: Final[str] = "api.agent.updated"
API_AGENT_DELETED: Final[str] = "api.agent.deleted"
API_AGENTS_REORDERED: Final[str] = "api.agents.reordered"

# Project mutations
API_PROJECT_CREATED: Final[str] = "api.project.created"
API_PROJECT_UPDATED: Final[str] = "api.project.updated"
API_PROJECT_DELETED: Final[str] = "api.project.deleted"
API_PROJECT_LISTED: Final[str] = "api.project.listed"
API_PROJECT_FETCH_FAILED: Final[str] = "api.project.fetch_failed"

# Artifact mutations
API_ARTIFACT_CREATED: Final[str] = "api.artifact.created"
API_ARTIFACT_UPDATED: Final[str] = "api.artifact.updated"
API_ARTIFACT_DELETED: Final[str] = "api.artifact.deleted"

# SSRF violation mutations (recorded by self-healing security flow,
# resolved by an operator via the dashboard).
API_SSRF_VIOLATION_RECORDED: Final[str] = "api.ssrf_violation.recorded"
API_SSRF_VIOLATION_STATUS_UPDATED: Final[str] = "api.ssrf_violation.status_updated"
API_SSRF_VIOLATION_LISTED: Final[str] = "api.ssrf_violation.listed"
API_SSRF_VIOLATION_FETCH_FAILED: Final[str] = "api.ssrf_violation.fetch_failed"

# Pagination / cursor
API_CURSOR_SECRET_EPHEMERAL: Final[str] = "api.cursor.secret.ephemeral"  # noqa: S105 -- event name, not a secret
API_CURSOR_INVALID: Final[str] = "api.cursor.invalid"

# Bridge-config validation
API_BRIDGE_CONFIG_REJECTED: Final[str] = "api.bridge_config.rejected"

# Shutdown
API_APP_SHUTDOWN_TIMEOUT: Final[str] = "api.app.shutdown.timeout"
API_SHUTDOWN_SIGNAL_RECEIVED: Final[str] = "api.shutdown.signal.received"
API_SHUTDOWN_HANDLER_SKIPPED: Final[str] = "api.shutdown.handler.skipped"
