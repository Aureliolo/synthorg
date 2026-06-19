"""HR event constants for structured logging.

Constants follow the ``hr.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

# ── Hiring ────────────────────────────────────────────────────────

HR_HIRING_REQUEST_CREATED: Final[str] = "hr.hiring.request_created"
HR_HIRING_CANDIDATE_GENERATED: Final[str] = "hr.hiring.candidate_generated"
HR_HIRING_APPROVAL_SUBMITTED: Final[str] = "hr.hiring.approval_submitted"
HR_HIRING_APPROVED: Final[str] = "hr.hiring.approved"
HR_HIRING_REJECTED: Final[str] = "hr.hiring.rejected"
HR_HIRING_INSTANTIATED: Final[str] = "hr.hiring.instantiated"

# ── Firing ────────────────────────────────────────────────────────

HR_FIRING_INITIATED: Final[str] = "hr.firing.initiated"
HR_FIRING_TASKS_REASSIGNED: Final[str] = "hr.firing.tasks_reassigned"
HR_FIRING_MEMORY_ARCHIVED: Final[str] = "hr.firing.memory_archived"
HR_FIRING_TEAM_NOTIFIED: Final[str] = "hr.firing.team_notified"
HR_FIRING_COMPLETE: Final[str] = "hr.firing.complete"

# ── Onboarding ───────────────────────────────────────────────────

HR_ONBOARDING_STARTED: Final[str] = "hr.onboarding.started"
HR_ONBOARDING_STEP_COMPLETE: Final[str] = "hr.onboarding.step_complete"
HR_ONBOARDING_COMPLETE: Final[str] = "hr.onboarding.complete"

# ── Registry ─────────────────────────────────────────────────────

HR_REGISTRY_AGENT_REGISTERED: Final[str] = "hr.registry.agent_registered"
HR_REGISTRY_AGENT_REMOVED: Final[str] = "hr.registry.agent_removed"
HR_REGISTRY_STATUS_UPDATED: Final[str] = "hr.registry.status_updated"
HR_REGISTRY_IDENTITY_UPDATED: Final[str] = "hr.registry.identity_updated"
HR_REGISTRY_IDENTITY_EVOLVED: Final[str] = "hr.registry.identity_evolved"
HR_REGISTRY_CLEARED: Final[str] = "hr.registry.cleared"

HR_AGENT_STATUS_TRANSITIONED: Final[str] = "hr.agent.status_transitioned"
"""Agent status transition (any persisted hop).

Emitted AFTER the registry write succeeds, carrying ``from_status``
/ ``to_status`` / ``agent_id``.  Complements terminal events
(``HR_ONBOARDING_COMPLETE``, ``HR_FIRING_COMPLETE``) which stay on
the terminal hop and remain the canonical "this is the final state"
markers."""

HIRING_REQUEST_STATUS_TRANSITIONED: Final[str] = "hr.hiring_request.status_transitioned"
"""Hiring request status transition (any persisted hop).

Emitted AFTER the in-memory request write succeeds, carrying
``from_status`` / ``to_status`` / ``request_id``.  Complements the
terminal ``HR_HIRING_INSTANTIATED`` event which stays on the final
hop; this constant covers the intermediate transitions (PENDING ->
APPROVED auto-approve, APPROVED -> INSTANTIATED) so the audit
chain reflects every status flip."""

# ── Error-path events ───────────────────────────────────────────

HR_HIRING_REQUEST_NOT_FOUND: Final[str] = "hr.hiring.request_not_found"
HR_HIRING_REQUEST_INVALID: Final[str] = "hr.hiring.request_invalid"
HR_HIRING_CANDIDATE_NOT_FOUND: Final[str] = "hr.hiring.candidate_not_found"
HR_HIRING_INSTANTIATION_FAILED: Final[str] = "hr.hiring.instantiation_failed"
HR_FIRING_REASSIGNMENT_FAILED: Final[str] = "hr.firing.reassignment_failed"
HR_FIRING_ARCHIVAL_FAILED: Final[str] = "hr.firing.archival_failed"
HR_FIRING_NOTIFICATION_FAILED: Final[str] = "hr.firing.notification_failed"
HR_ARCHIVAL_ENTRY_FAILED: Final[str] = "hr.archival.entry_failed"

# ── Activity timeline ──────────────────────────────────────────

HR_ACTIVITY_REDACTION_MISMATCH: Final[str] = "hr.activity.redaction_pattern_mismatch"
HR_ACTIVITY_AGENT_FETCHED: Final[str] = "hr.activity.agent_fetched"
HR_ACTIVITY_SOURCE_FETCH_FAILED: Final[str] = "hr.activity.source_fetch_failed"
HR_ACTIVITY_INVALID_REQUEST: Final[str] = "hr.activity.invalid_request"
HR_ACTIVITY_LIFECYCLE_CAP_HIT: Final[str] = "hr.activity.lifecycle_cap_hit"

# ── Health aggregation ────────────────────────────────────────

HR_AGENT_HEALTH_COMPUTED: Final[str] = "hr.agent.health_computed"
HR_AGENT_HEALTH_FAILED: Final[str] = "hr.agent.health_failed"

# ── Training sessions ─────────────────────────────────────────

HR_TRAINING_SESSION_RECORDED: Final[str] = "hr.training.session_recorded"
HR_TRAINING_SESSION_LISTED: Final[str] = "hr.training.session_listed"
HR_TRAINING_SESSION_RECORD_FAILED: Final[str] = "hr.training.session_record_failed"
HR_TRAINING_SESSION_INVALID_REQUEST: Final[str] = "hr.training.session_invalid_request"

# ── Pruning ────────────────────────────────────────────────────

HR_PRUNING_CYCLE_STARTED: Final[str] = "hr.pruning.cycle_started"
HR_PRUNING_EVALUATION_COMPLETE: Final[str] = "hr.pruning.evaluation_complete"
HR_PRUNING_AGENT_ELIGIBLE: Final[str] = "hr.pruning.agent_eligible"
HR_PRUNING_APPROVAL_SUBMITTED: Final[str] = "hr.pruning.approval_submitted"
HR_PRUNING_APPROVAL_DEDUP_SKIP: Final[str] = "hr.pruning.approval_dedup_skip"
HR_PRUNING_APPROVED: Final[str] = "hr.pruning.approved"
HR_PRUNING_REJECTED: Final[str] = "hr.pruning.rejected"
HR_PRUNING_OFFBOARDED: Final[str] = "hr.pruning.offboarded"
HR_PRUNING_CYCLE_COMPLETE: Final[str] = "hr.pruning.cycle_complete"
HR_PRUNING_POLICY_ERROR: Final[str] = "hr.pruning.policy_error"
HR_PRUNING_SCHEDULER_STARTED: Final[str] = "hr.pruning.scheduler_started"
HR_PRUNING_SCHEDULER_STOPPED: Final[str] = "hr.pruning.scheduler_stopped"
HR_PRUNING_PERSISTENCE_FAILED: Final[str] = "hr.pruning.persistence_failed"
HR_PRUNING_REQUESTS_REHYDRATED: Final[str] = "hr.pruning.requests_rehydrated"

# Status-machine event for PruningRequest. Mirrors ApprovalStatus hops on
# the underlying ApprovalItem; emitted at construction (from_status=None)
# and at every status sync site.
PRUNING_REQUEST_STATUS_TRANSITIONED: Final[str] = (
    "hr.pruning_request.status_transitioned"
)

# -- Scaling -----------------------------------------------------------------

HR_SCALING_TRIGGER_REQUESTED: Final[str] = "hr.scaling.trigger_requested"
HR_SCALING_TRIGGER_SKIPPED: Final[str] = "hr.scaling.trigger_skipped"
HR_SCALING_CONTEXT_BUILT: Final[str] = "hr.scaling.context_built"
HR_SCALING_STRATEGY_EVALUATED: Final[str] = "hr.scaling.strategy_evaluated"
HR_SCALING_DECISIONS_MERGED: Final[str] = "hr.scaling.decisions_merged"
HR_SCALING_GUARD_APPLIED: Final[str] = "hr.scaling.guard_applied"
HR_SCALING_DECISION_APPROVED: Final[str] = "hr.scaling.decision_approved"
HR_SCALING_DECISION_REJECTED: Final[str] = "hr.scaling.decision_rejected"
HR_SCALING_EXECUTION_STARTED: Final[str] = "hr.scaling.execution_started"
HR_SCALING_EXECUTED: Final[str] = "hr.scaling.executed"
HR_SCALING_EXECUTION_FAILED: Final[str] = "hr.scaling.execution_failed"
HR_SCALING_CYCLE_STARTED: Final[str] = "hr.scaling.cycle_started"
HR_SCALING_CYCLE_COMPLETE: Final[str] = "hr.scaling.cycle_complete"
HR_SCALING_SERVICE_STARTED: Final[str] = "hr.scaling.service_started"
HR_SCALING_SERVICE_STOPPED: Final[str] = "hr.scaling.service_stopped"
HR_SCALING_SIGNAL_COLLECTION_DEGRADED: Final[str] = (
    "hr.scaling.signal_collection_degraded"
)
HR_SCALING_MANUAL_TRIGGER_REQUESTED: Final[str] = "hr.scaling.manual_trigger_requested"
HR_SCALING_CONFIG_VALIDATION_FAILED: Final[str] = "hr.scaling.config_validation_failed"
HR_SCALING_STRATEGY_VALIDATION_FAILED: Final[str] = (
    "hr.scaling.strategy_validation_failed"
)
HR_SCALING_MODEL_VALIDATION_FAILED: Final[str] = "hr.scaling.model_validation_failed"
HR_SCALING_SERVICE_VALIDATION_FAILED: Final[str] = (
    "hr.scaling.service_validation_failed"
)
HR_SCALING_CONTROLLER_SERVICE_MISSING: Final[str] = (
    "hr.scaling.controller_service_missing"
)
HR_SCALING_STRATEGY_TOGGLED: Final[str] = "hr.scaling.strategy_toggled"
HR_SCALING_PRIORITY_ORDER_UPDATED: Final[str] = "hr.scaling.priority_order_updated"
HR_SCALING_FACTORY_ASSEMBLED: Final[str] = "hr.scaling.factory_assembled"
HR_SCALING_CONTROLLER_INVALID_REQUEST: Final[str] = (
    "hr.scaling.controller_invalid_request"
)

HR_PERFORMANCE_CURRENCY_INVARIANT_VIOLATED: Final[str] = (
    "hr.performance.currency_invariant_violated"
)
