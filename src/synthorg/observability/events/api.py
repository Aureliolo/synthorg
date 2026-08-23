"""API event constants."""

from typing import Final

API_CURSOR_DECODE_FAILED: Final[str] = "api.cursor.decode_failed"
API_RATE_LIMIT_BACKEND_UNSUPPORTED: Final[str] = "api.rate_limit.backend.unsupported"
API_REQUEST_STARTED: Final[str] = "api.request.started"
API_STATE_SERVICE_ATTACHED: Final[str] = "api.state.service_attached"
API_REQUEST_COMPLETED: Final[str] = "api.request.completed"
API_REQUEST_ERROR: Final[str] = "api.request.error"
API_HEALTH_CHECK: Final[str] = "api.health.check"
# A live capability gate (``ensure_feature_enabled``) blocked a request because
# an operator has the feature's ``<namespace>.<key>`` switch off. Carries
# ``namespace`` / ``key`` / ``feature_label`` so a single event covers every
# gated feature (research, knowledge, chief-of-staff chat, ...).
API_FEATURE_GATE_BLOCKED: Final[str] = "api.feature.gate_blocked"
API_APP_STARTUP: Final[str] = "api.app.startup"
API_APP_SHUTDOWN: Final[str] = "api.app.shutdown"
# A security-critical durable subsystem silently degraded to in-memory-only
# at boot (its persistence wiring failed). Distinct from the generic
# startup event so operators can alert on durability loss specifically.
API_AUDIT_CHAIN_PERSISTENCE_DEGRADED: Final[str] = (
    "api.app.audit_chain_persistence_degraded"
)
API_APP_DRAIN_STARTED: Final[str] = "api.app.drain.started"
API_APP_DRAIN_COMPLETED: Final[str] = "api.app.drain.completed"
API_APP_DRAIN_TIMEOUT: Final[str] = "api.app.drain.timeout"
API_APP_DRAIN_SEND_FAILED: Final[str] = "api.app.drain.send_failed"
API_ETAG_CACHE_HIT: Final[str] = "api.etag.cache_hit"
API_WS_CONNECTED: Final[str] = "api.ws.connected"
API_WS_DISCONNECTED: Final[str] = "api.ws.disconnected"
API_AUTH_CONTEXT_BOUND: Final[str] = "api.auth.context_bound"
API_AUTH_CONTEXT_SKIPPED: Final[str] = "api.auth.context_skipped"
API_AUTH_CONTEXT_MISSING: Final[str] = "api.auth.context_missing"
API_GUARD_DENIED: Final[str] = "api.guard.denied"
API_GUARD_DEGRADED_AUTH: Final[str] = "api.guard.degraded_auth"
API_BUS_BRIDGE_SUBSCRIBE_FAILED: Final[str] = "api.bus_bridge.subscribe.failed"
API_BUS_BRIDGE_POLL_ERROR: Final[str] = "api.bus_bridge.poll.error"
API_BUS_BRIDGE_DRAIN_RESOLVE_ERROR: Final[str] = "api.bus_bridge.drain.resolve_error"
API_WS_INVALID_MESSAGE: Final[str] = "api.ws.invalid_message"
API_WS_SUBSCRIBE: Final[str] = "api.ws.subscribe"
API_WS_UNSUBSCRIBE: Final[str] = "api.ws.unsubscribe"
API_WS_UNKNOWN_ACTION: Final[str] = "api.ws.unknown_action"
API_RESOURCE_NOT_FOUND: Final[str] = "api.resource.not_found"
API_TASK_UPDATED: Final[str] = "api.task.updated"
API_TASK_MONEY_CEILING_REFUSED: Final[str] = "api.task.money_ceiling_refused"
API_TASK_DELETED: Final[str] = "api.task.deleted"
API_TASK_DELETE_REFUSED: Final[str] = "api.task.delete_refused"
API_TASK_CANCELLED: Final[str] = "api.task.cancelled"
API_APPROVAL_CREATED: Final[str] = "api.approval.created"
# api.approval.approved / api.approval.rejected moved to events.security
# (SECURITY_APPROVAL_APPROVED / SECURITY_APPROVAL_REJECTED) so the audit
# chain signs the human decision.
API_APPROVAL_EXPIRED: Final[str] = "api.approval.expired"
# Distinct from the expiry above: that one ran out of time, this one lost its
# subject. Reading them under one name would make a deletion look like a
# deadline nobody met.
API_APPROVAL_RETIRED: Final[str] = "api.approval.retired"
# The delete the retirement was for did not happen, so the approval is pending
# again. Logged rather than silent: an operator watching the queue sees a row
# leave and come back, and this is what says why.
API_APPROVAL_RESTORED: Final[str] = "api.approval.restored"
API_APPROVAL_EXPIRE_BATCH_FAILED: Final[str] = "api.approval.expire_batch_failed"
API_APPROVAL_ADD_CALLBACK_FAILED: Final[str] = "api.approval.add_callback_failed"
API_APPROVAL_EXPIRE_CALLBACK_FAILED: Final[str] = "api.approval.expire_callback_failed"
API_APPROVAL_PUBLISH_FAILED: Final[str] = "api.approval.publish_failed"
API_APPROVAL_CONFLICT: Final[str] = "api.approval.conflict"
API_APPROVAL_STORE_CLEARED: Final[str] = "api.approval.store_cleared"
API_APPROVAL_REPO_ATTACHED: Final[str] = "api.approval.repo_attached"
API_APPROVAL_REPO_ATTACH_REFUSED: Final[str] = "api.approval.repo_attach_refused"
API_APPROVAL_REPO_SAVED: Final[str] = "api.approval.repo_saved"
API_APPROVAL_REPO_FETCHED: Final[str] = "api.approval.repo_fetched"
API_APPROVAL_REPO_LISTED: Final[str] = "api.approval.repo_listed"
API_APPROVAL_ENRICH_FAILED: Final[str] = "api.approval.enrich_failed"
API_APPROVAL_REPO_DELETED: Final[str] = "api.approval.repo_deleted"
# Emitted by every read boundary that resolves a reference to a name. Kept apart
# from the approval-specific event so that alerting on it means "a name would not
# resolve" rather than "an approval failed to enrich".
API_READ_NAME_RESOLVE_FAILED: Final[str] = "api.read.name_resolve_failed"
API_APPROVAL_REPO_FAILED: Final[str] = "api.approval.repo_failed"
API_BRIDGE_CHANNEL_DEAD: Final[str] = "api.bus_bridge.channel_dead"
API_WS_TRANSPORT_ERROR: Final[str] = "api.ws.transport_error"
API_WS_SEND_FAILED: Final[str] = "api.ws.send_failed"
API_SERVICE_UNAVAILABLE: Final[str] = "api.service.unavailable"
API_SERVICE_AUTO_WIRED: Final[str] = "api.service.auto_wired"
API_SERVICE_AUTO_WIRE_FAILED: Final[str] = "api.service.auto_wire_failed"
API_MEETINGS_WIRING_DEFERRED: Final[str] = "api.meetings.wiring_deferred"
# auth.failed / token_issued / setup_complete / password_changed are
# audit-chained security decisions and live in events.security as
# SECURITY_AUTH_*. A *successful* per-request authentication is NOT a
# decision worth signing -- it fires on every authenticated request, so
# chaining it would grow the hash chain unbounded. It stays here under
# api.* (unsigned). The audited grant is the login itself
# (SECURITY_AUTH_TOKEN_ISSUED), emitted once per credential exchange.
API_AUTH_SUCCESS: Final[str] = "api.auth.success"
API_AUTH_GUARD_SKIPPED: Final[str] = "api.auth.guard_skipped"
API_TASK_TRANSITION_FAILED: Final[str] = "api.task.transition_failed"
API_TASK_MUTATION_FAILED: Final[str] = "api.task.mutation_failed"
API_TASK_REJECTED_NO_PROVIDER: Final[str] = "api.task.rejected_no_provider"
API_TASK_BOARD_SUBMITTED: Final[str] = "api.task.board.submitted"
API_TASK_BOARD_REJECTED_NO_ADAPTER: Final[str] = "api.task.board.rejected_no_adapter"
API_TASK_BOARD_PIPELINE_FAILED: Final[str] = "api.task.board.pipeline_failed"
API_AUTH_SYSTEM_USER_ENSURED: Final[str] = "api.auth.system_user_ensured"
API_AUTH_FALLBACK: Final[str] = "api.auth.fallback"
API_AUTH_DISCRIMINATOR_UNKNOWN_DETAIL: Final[str] = (
    "api.auth.discriminator.unknown_detail"
)
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
API_WS_TICKET_LIMIT_EXCEEDED: Final[str] = "api.ws.ticket_limit_exceeded"
API_AUDIT_RETENTION: Final[str] = "api.audit.retention"
API_FLIGHT_RECORDER_RETENTION: Final[str] = "api.flight_recorder.retention"
API_WS_AUTH_STAGE: Final[str] = "api.ws.auth_stage"
API_WS_AUTH_OK: Final[str] = "api.ws.auth_ok"
API_WS_PING: Final[str] = "api.ws.ping"
API_WS_EVENT_DROPPED: Final[str] = "api.ws.event_dropped"
API_WS_BACKPRESSURE_DROPPED: Final[str] = "api.ws.backpressure_dropped"
API_WS_FRAME_TIMEOUT: Final[str] = "api.ws.frame_timeout"
API_WS_REVALIDATION_BUDGET_EXHAUSTED: Final[str] = (
    "api.ws.revalidation_budget_exhausted"
)

# SSE streaming
API_SSE_PULL_MODEL_FAILED: Final[str] = "api.sse.pull_model_failed"
API_SSE_INVALID_LAST_EVENT_ID: Final[str] = "api.sse.invalid_last_event_id"
API_DASHBOARD_SSE_SUBSCRIBED: Final[str] = "api.dashboard_sse.subscribed"
API_DASHBOARD_SSE_UNSUBSCRIBED: Final[str] = "api.dashboard_sse.unsubscribed"
API_DASHBOARD_SSE_UNSUBSCRIBE_FAILED: Final[str] = "api.dashboard_sse.unsub_failed"
API_DASHBOARD_SSE_FRAME_INVALID: Final[str] = "api.dashboard_sse.frame_invalid"
API_DASHBOARD_SSE_UNAUTHENTICATED: Final[str] = "api.dashboard_sse.unauthenticated"
API_DASHBOARD_SSE_FEED_UNAVAILABLE: Final[str] = "api.dashboard_sse.feed_unavailable"
API_MODEL_OPERATION_FAILED: Final[str] = "api.model.operation_failed"
API_OPENAPI_SCHEMA_ENHANCED: Final[str] = "api.openapi.schema_enhanced"
API_RESOURCE_CONFLICT: Final[str] = "api.resource.conflict"
API_VALIDATION_FAILED: Final[str] = "api.validation.failed"
API_SETTINGS_BACKEND_RECOVERED: Final[str] = "api.settings.backend_recovered"
API_ASGI_MISSING_STATUS: Final[str] = "api.asgi.missing_status"
API_AGENT_PERFORMANCE_QUERIED: Final[str] = "api.agent.performance_queried"
API_AGENT_ACTIVITY_QUERIED: Final[str] = "api.agent.activity_queried"
API_AGENT_HISTORY_QUERIED: Final[str] = "api.agent.history_queried"
API_DEPARTMENT_HEALTH_QUERIED: Final[str] = "api.department.health_queried"
API_PROVIDER_HEALTH_QUERIED: Final[str] = "api.provider.health_queried"
API_PROVIDER_HEALTH_RECHECKED: Final[str] = "api.provider.health_rechecked"
API_PROVIDER_HEALTH_RECHECK_REFUSED: Final[str] = "api.provider.health_recheck_refused"
#: Emitted at WARNING when the reconcile pass a recheck triggers could not
#: run. The recheck's own verdict is unaffected; what is lost is the immediate
#: re-attempt of subsystems that were blocked on the provider, which the
#: periodic sweep still picks up.
API_PROVIDER_RECHECK_RECONCILE_FAILED: Final[str] = (
    "api.provider.recheck_reconcile_failed"
)
#: Emitted at WARNING the first time a subsystem is observed stuck (blocked or
#: failed) on a given condition. The pull-only ``GET /subsystems`` answers this
#: for whoever asks; this is what reaches an operator who is not asking.
API_SUBSYSTEM_ESCALATED: Final[str] = "api.subsystem.escalated"
#: Emitted at WARNING when the operator notification for a stuck subsystem
#: could not be delivered. The condition itself is still recorded by
#: ``API_SUBSYSTEM_ESCALATED``.
API_SUBSYSTEM_ESCALATION_FAILED: Final[str] = "api.subsystem.escalation_failed"
#: Emitted at ERROR the first time a stuck subsystem is found with no
#: notification dispatcher to report it to. Every shipped launcher wires one
#: during construction, so this names an assembly that cannot escalate at all
#: rather than one whose sinks happen to be quiet.
API_SUBSYSTEM_ESCALATION_UNROUTED: Final[str] = "api.subsystem.escalation_unrouted"
#: Emitted at WARNING when a stuck subsystem's notification reached the
#: dispatcher and no sink accepted it: switched off, filtered below the
#: severity floor, no sink registered, or shutting down. The condition stays
#: unremembered so the next pass tries again, and this names why it had to.
API_SUBSYSTEM_ESCALATION_UNDELIVERED: Final[str] = (
    "api.subsystem.escalation_undelivered"
)
API_PROVIDER_USAGE_ENRICHMENT_FAILED: Final[str] = (
    "api.provider.usage_enrichment_failed"
)
API_ACTIVITY_FEED_QUERIED: Final[str] = "api.activity.feed_queried"
API_MEETING_TRIGGERED: Final[str] = "api.meeting.triggered"
API_BUDGET_RECORDS_LISTED: Final[str] = "api.budget.records_listed"
API_BUDGET_CALL_ANALYTICS_QUERIED: Final[str] = "api.budget.call_analytics_queried"
API_BUDGET_PROMPT_CLASS_BREAKDOWN_QUERIED: Final[str] = (
    "api.budget.prompt_class_breakdown_queried"
)
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
# In-memory lockout cache rehydration after process restart. This is an
# operational housekeeping event (no NEW lockout decision is being
# recorded -- the underlying lockout was already audit-chained when the
# threshold tripped on the original request). Stays under api.* so the
# audit chain does not log duplicate lockout decisions on every restart.
API_AUTH_LOCKOUT_RESTORED: Final[str] = "api.auth.lockout_restored"

# Refresh tokens
# api.auth.refresh_created / consumed / rejected / revoked moved to
# events.security as SECURITY_AUTH_REFRESH_* (audit-chained).
API_AUTH_REFRESH_CLEANUP: Final[str] = "api.auth.refresh_cleanup"

# Persistence-layer failures inside the auth repositories. These are
# storage errors (DB connection drop, constraint violation, rollback
# failure) -- NOT auth decisions -- so they stay under api.* and are
# NOT signed by the audit chain. Repository code emits these on the
# rollback branch to keep the operator-visible failure trail intact
# without polluting the cryptographic decision record.
API_AUTH_REFRESH_PERSISTENCE_ERROR: Final[str] = "api.auth.refresh.persistence_error"
API_AUTH_LOCKOUT_PERSISTENCE_ERROR: Final[str] = "api.auth.lockout.persistence_error"
API_AUTH_SESSION_PERSISTENCE_ERROR: Final[str] = "api.auth.session.persistence_error"

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
API_INTERRUPTS_QUERIED: Final[str] = "api.interrupts.queried"
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
# An agent names a (provider, model) pair that no configured provider offers:
# a stale binding left by a removed or renamed model, distinct from an agent
# that simply has no model assigned yet.
API_AGENT_MODEL_BINDING_UNRESOLVED: Final[str] = "api.agent.model_binding_unresolved"
# Provider config could not be read while decorating an agent response. The
# agent data itself is intact; only the derived capability view is missing.
API_AGENT_CAPABILITIES_UNAVAILABLE: Final[str] = "api.agent.capabilities_unavailable"

# Project mutations
API_PROJECT_CREATED: Final[str] = "api.project.created"
API_PROJECT_UPDATED: Final[str] = "api.project.updated"
API_PROJECT_AUTONOMY_MODE_CHANGED: Final[str] = "api.project.autonomy_mode_changed"
API_PROJECT_DELETED: Final[str] = "api.project.deleted"
API_PROJECT_CASCADE_CONTENDED: Final[str] = "api.project.cascade.contended"
API_PROJECT_CASCADE_COMPLETED: Final[str] = "api.project.cascade.completed"
API_PROJECT_LISTED: Final[str] = "api.project.listed"
API_PROJECT_FETCH_FAILED: Final[str] = "api.project.fetch_failed"

# Plan review (durable, revisable decomposition plans)
API_PLAN_LISTED: Final[str] = "api.plan.listed"
API_PLAN_LIST_FAILED: Final[str] = "api.plan.list_failed"
API_PLAN_FETCH_FAILED: Final[str] = "api.plan.fetch_failed"
API_PLAN_UPDATED: Final[str] = "api.plan.updated"
API_PLAN_UPDATE_FAILED: Final[str] = "api.plan.update_failed"
API_PLAN_CHANGES_REQUESTED: Final[str] = "api.plan.changes_requested"
API_PLAN_CHANGES_REQUEST_FAILED: Final[str] = "api.plan.changes_request_failed"
API_PLAN_CHANGES_REPLANNED: Final[str] = "api.plan.changes_replanned"
API_PLAN_STATUS_TRANSITIONED: Final[str] = "api.plan.status_transitioned"
API_PLAN_SUCCESSOR_OPENED: Final[str] = "api.plan.successor_opened"
API_PLAN_REPLANNED: Final[str] = "api.plan.replanned"
API_PLAN_REPLAN_WORK_TERMINATED: Final[str] = "api.plan.replan_work_terminated"
API_PLAN_REPLAN_PARKED: Final[str] = "api.plan.replan_parked"
API_PLAN_REPLAN_PARK_FAILED: Final[str] = "api.plan.replan_park_failed"
API_PLAN_REPLAN_ROLLBACK_RELINK_FAILED: Final[str] = (
    "api.plan.replan_rollback_relink_failed"
)
API_PLAN_REPLAN_ROLLBACK_UNCONFIRMED: Final[str] = (
    "api.plan.replan_rollback_unconfirmed"
)
API_PLAN_REPLAN_ROLLBACK_DELETE_FAILED: Final[str] = (
    "api.plan.replan_rollback_delete_failed"
)
API_PLAN_TRANSITION_REJECTED: Final[str] = "api.plan.transition_rejected"
API_PLAN_COMMENT_ADDED: Final[str] = "api.plan.comment_added"
API_PLAN_DELETED: Final[str] = "api.plan.deleted"
API_PLAN_DELETE_REFUSED: Final[str] = "api.plan.delete_refused"

# Bulk deletion (one operator action over a selected set)
API_BULK_DELETE_PARTIAL: Final[str] = "api.bulk_delete.partial"
#: One row of a selection refused. The client is told only the error's own
#: default message, so this is where the specific cause is kept.
API_BULK_DELETE_ROW_REFUSED: Final[str] = "api.bulk_delete.row_refused"

# Artifact mutations
API_ARTIFACT_CREATED: Final[str] = "api.artifact.created"
API_ARTIFACT_UPDATED: Final[str] = "api.artifact.updated"
API_ARTIFACT_DELETED: Final[str] = "api.artifact.deleted"

# SSRF violation read-side events (mutations live on the security audit
# chain via SECURITY_SSRF_VIOLATION_* in observability/events/security.py
# so signed audit consumers see the WHO+WHEN of recordings + resolutions).
API_SSRF_VIOLATION_LISTED: Final[str] = "api.ssrf_violation.listed"
API_SSRF_VIOLATION_FETCH_FAILED: Final[str] = "api.ssrf_violation.fetch_failed"

# Read-side list tracing for controllers that previously had no
# handler-level observability on the happy path.
API_CLIENT_LISTED: Final[str] = "api.client.listed"
API_TASK_LISTED: Final[str] = "api.task.listed"
API_STEERING_LISTED: Final[str] = "api.steering.listed"
API_RISK_OVERRIDE_LISTED: Final[str] = "api.risk_override.listed"
# CEO-only risk-tier override state changes -- audit-relevant, so logged at
# INFO even though the durable override row is itself the audit artefact.
API_RISK_OVERRIDE_CREATED: Final[str] = "api.risk_override.created"
API_RISK_OVERRIDE_REVOKED: Final[str] = "api.risk_override.revoked"
API_EXPERIMENT_VARIANT_REGISTERED: Final[str] = "api.experiment.variant_registered"
API_BUDGET_CFO_QUERIED: Final[str] = "api.budget_cfo.queried"

# Pagination / cursor
API_CURSOR_SECRET_EPHEMERAL: Final[str] = "api.cursor.secret.ephemeral"  # noqa: S105 -- event name, not a secret
API_CURSOR_INVALID: Final[str] = "api.cursor.invalid"

# Bridge-config validation
API_BRIDGE_CONFIG_REJECTED: Final[str] = "api.bridge_config.rejected"
# Bridge-config resolver-side failure -- caller fell back to defaults so
# the dependent endpoint stays available during transient settings
# outages or bad stored values.
API_BRIDGE_CONFIG_RESOLVE_FAILED: Final[str] = "api.bridge_config.resolve_failed"

# Per-request lifecycle lock registry
REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD: Final[str] = (
    "api.request_lock.release_skipped_while_held"
)
REQUEST_LOCK_EVICTED_AT_CAP: Final[str] = "api.request_lock.evicted_at_cap"

# Shutdown
API_APP_SHUTDOWN_TIMEOUT: Final[str] = "api.app.shutdown.timeout"
API_SHUTDOWN_SIGNAL_RECEIVED: Final[str] = "api.shutdown.signal.received"
API_SHUTDOWN_HANDLER_SKIPPED: Final[str] = "api.shutdown.handler.skipped"

# User presence (WS / auth controller)
USER_PRESENCE_CONNECT: Final[str] = "user.presence.connect"
USER_PRESENCE_DISCONNECT: Final[str] = "user.presence.disconnect"

# Settings import-source classification.
API_SETTINGS_VALIDATION_FAILED: Final[str] = "api.settings.validation_failed"

# Boundary typed-parse helper for stringly-typed entry-point migration.
API_BOUNDARY_VALIDATION_FAILED: Final[str] = "api.boundary.validation_failed"
"""Emitted when ``synthorg.core.boundary.parse_typed`` rejects a payload
at a registered API boundary (MCP handler args, JWT decode, WebSocket
control message, audit-chain payload, A2A JSON-RPC params, settings
export). Carries the boundary name, error count, the redacted error
description, the failing field locations, and a ``truncated`` flag so
validation failures can be diagnosed without re-running the request."""

# Audit chain entries written from controllers.
AGENT_IDENTITY_MODIFIED: Final[str] = "audit.agent.identity_modified"
AGENT_DELETION_REQUESTED: Final[str] = "audit.agent.deletion_requested"
"""Pre-delete intent audit -- fires BEFORE persistence so the trail
captures the operator's request even if the delete itself fails.
``AGENT_DELETED_AUDIT`` complements it by firing AFTER success."""
AGENT_DELETED_AUDIT: Final[str] = "audit.agent.deleted"
"""Post-delete confirmation audit -- fires only after persistence
delete succeeds.  ``AGENT_DELETION_REQUESTED`` covers the pre-delete
intent log."""
WORKFLOW_DEFINITION_CHANGE_REQUESTED: Final[str] = (
    "audit.workflow_definition.change_requested"
)
"""Pre-mutation intent audit for workflow-definition CRUD -- fires
BEFORE persistence so the trail captures intent on failure."""
WORKFLOW_DEFINITION_CHANGED: Final[str] = "audit.workflow_definition.changed"
"""Post-mutation confirmation audit -- fires only after the
workflow-definition persistence operation succeeds."""
