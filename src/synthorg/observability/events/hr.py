"""HR event constants for structured logging.

Constants follow the ``hr.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

# ── Hiring ────────────────────────────────────────────────────────

HR_HIRING_REQUEST_CREATED: Final[str] = "hr.hiring.request_created"
#: A half-opened request was removed before anyone saw it. Distinct from
#: ``HR_HIRING_REJECTED``, which records a decision a person took.
HR_HIRING_REQUEST_DISCARDED: Final[str] = "hr.hiring.request_discarded"
HR_HIRING_CANDIDATE_GENERATED: Final[str] = "hr.hiring.candidate_generated"
HR_HIRING_APPROVAL_SUBMITTED: Final[str] = "hr.hiring.approval_submitted"
HR_HIRING_APPROVAL_ORPHANED: Final[str] = "hr.hiring.approval_orphaned"
HR_HIRING_APPROVED: Final[str] = "hr.hiring.approved"
HR_HIRING_REJECTED: Final[str] = "hr.hiring.rejected"
HR_HIRING_INSTANTIATED: Final[str] = "hr.hiring.instantiated"
HR_HIRING_MODEL_UNSET: Final[str] = "hr.hiring.model_unset"
"""The approved hire carries no pair, so instantiation refused.

A hire that registered against a placeholder provider would join the roster
looking staffed and fail every dispatch, so the absence surfaces here and at
the caller as a 503."""

HR_HIRING_MODEL_PROPOSED: Final[str] = "hr.hiring.model_proposed"
"""Pairs were offered for an operator to bind this hire to.

Proposed by the same capability matcher the setup wizard runs, scored against
the operator's own configured models, one option per spend profile."""

HR_HIRING_MODEL_CHOSEN: Final[str] = "hr.hiring.model_chosen"
"""The operator bound the hire to a pair other than the recommended one.

Recorded because the choice is the operator overriding a proposal, and the
approval's own reason text says which option without saying it was an
override."""

# ── Staffing (who holds a role, and who fits a piece of work) ─────

HR_STAFFING_SELECTED: Final[str] = "hr.staffing.selected"
HR_STAFFING_WIDENED: Final[str] = "hr.staffing.widened"
HR_STAFFING_UNDER_CAPABILITY: Final[str] = "hr.staffing.under_capability"
HR_STAFFING_NO_HOLDER: Final[str] = "hr.staffing.no_holder"

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
HR_REGISTRY_LISTENER_FAILED: Final[str] = "hr.registry.listener_failed"
"""The roster-change observer raised.

The observer is a fast-path notification, so its failure costs the consumer
one cadence and must never unwind the mutation that already committed.
"""

HR_AGENT_STATUS_TRANSITIONED: Final[str] = "hr.agent.status_transitioned"
"""Agent lifecycle-status transition (any persisted ``AgentStatus`` hop).

Emitted AFTER the registry write succeeds, carrying ``from_status``
/ ``to_status`` / ``agent_id``.  Complements terminal events
(``HR_ONBOARDING_COMPLETE``, ``HR_FIRING_COMPLETE``) which stay on
the terminal hop and remain the canonical "this is the final state"
markers. Autonomy-level changes ride
``HR_AGENT_AUTONOMY_LEVEL_TRANSITIONED`` instead, so the two enum spaces
(``AgentStatus`` vs ``AutonomyLevel``) never collide on one event stream."""

HR_AGENT_AUTONOMY_LEVEL_TRANSITIONED: Final[str] = (
    "hr.agent.autonomy_level_transitioned"
)
"""Agent autonomy-level transition (any persisted ``AutonomyLevel`` hop).

Emitted AFTER the snapshot write succeeds, carrying ``from_level`` /
``to_level`` / ``agent_id`` / ``saved_by``. Distinct from
``HR_AGENT_STATUS_TRANSITIONED`` so a consumer can branch on autonomy
promotions / demotions without conflating them with lifecycle-status hops."""

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
HR_OFFBOARDING_PERFORMANCE_EVICTION_FAILED: Final[str] = (
    "hr.offboarding.performance_eviction_failed"
)
HR_ARCHIVAL_ENTRY_FAILED: Final[str] = "hr.archival.entry_failed"
HR_STAFFING_REQUIREMENT_FLOORED: Final[str] = "hr.staffing.requirement_floored"
HR_HIRING_RISK_TIER_MISSING: Final[str] = "hr.hiring.risk_tier_missing"
HR_HIRING_ALREADY_REGISTERED: Final[str] = "hr.hiring.already_registered"
HR_HIRING_PERSIST_FAILED: Final[str] = "hr.hiring.persist_failed"
HR_HIRING_REQUESTS_HYDRATED: Final[str] = "hr.hiring.requests_hydrated"

# ── Activity timeline ──────────────────────────────────────────

HR_ACTIVITY_REDACTION_MISMATCH: Final[str] = "hr.activity.redaction_pattern_mismatch"
HR_ACTIVITY_AGENT_FETCHED: Final[str] = "hr.activity.agent_fetched"
HR_ACTIVITY_SOURCE_FETCH_FAILED: Final[str] = "hr.activity.source_fetch_failed"
HR_ACTIVITY_INVALID_REQUEST: Final[str] = "hr.activity.invalid_request"
HR_ACTIVITY_LIFECYCLE_CAP_HIT: Final[str] = "hr.activity.lifecycle_cap_hit"

# ── Health aggregation ────────────────────────────────────────

HR_AGENT_HEALTH_COMPUTED: Final[str] = "hr.agent.health_computed"
HR_AGENT_HEALTH_FAILED: Final[str] = "hr.agent.health_failed"

# ── Availability derived from the bound model's serviceability ─

HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE: Final[str] = (
    "hr.agent.unavailable_model_unserviceable"
)
HR_AGENT_AVAILABLE_MODEL_RECOVERED: Final[str] = "hr.agent.available_model_recovered"

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
