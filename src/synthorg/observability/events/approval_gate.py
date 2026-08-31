"""Approval gate event constants."""

from typing import Final

APPROVAL_GATE_INITIALIZED: Final[str] = "approval_gate.initialized"
APPROVAL_GATE_ESCALATION_DETECTED: Final[str] = "approval_gate.escalation.detected"
APPROVAL_GATE_ESCALATION_FAILED: Final[str] = "approval_gate.escalation.failed"
APPROVAL_GATE_RISK_CLASSIFIED: Final[str] = "approval_gate.risk.classified"
APPROVAL_GATE_RISK_CLASSIFY_FAILED: Final[str] = "approval_gate.risk.classify_failed"
APPROVAL_GATE_LOOP_WIRING_WARNING: Final[str] = "approval_gate.loop_wiring_warning"
APPROVAL_GATE_CONTEXT_PARKED: Final[str] = "approval_gate.context.parked"
APPROVAL_GATE_CONTEXT_PARK_FAILED: Final[str] = "approval_gate.context.park_failed"
APPROVAL_GATE_PARK_TASKLESS: Final[str] = "approval_gate.park.taskless"
APPROVAL_GATE_RESUME_STARTED: Final[str] = "approval_gate.resume.started"
APPROVAL_GATE_CONTEXT_RESUMED: Final[str] = "approval_gate.context.resumed"
APPROVAL_GATE_RESUME_FAILED: Final[str] = "approval_gate.resume.failed"
#: A just-persisted decision's reread hit a transient error and is being
#: retried before any resume flow gives up ownership of it.
APPROVAL_GATE_REREAD_RETRIED: Final[str] = "approval_gate.reread.retried"
APPROVAL_GATE_RESUME_DELETE_FAILED: Final[str] = "approval_gate.resume.delete_failed"
APPROVAL_GATE_RESUME_TRIGGERED: Final[str] = "approval_gate.resume.triggered"
APPROVAL_GATE_RESUME_DISPATCHED: Final[str] = "approval_gate.resume.dispatched"
APPROVAL_GATE_RESUME_COMPLETED: Final[str] = "approval_gate.resume.completed"
APPROVAL_GATE_NO_PARKED_CONTEXT: Final[str] = "approval_gate.no_parked_context"
APPROVAL_GATE_REVIEW_CREATED: Final[str] = "approval_gate.review.created"
APPROVAL_GATE_REVIEW_COMPLETED: Final[str] = "approval_gate.review.completed"
APPROVAL_GATE_REVIEW_REWORK: Final[str] = "approval_gate.review.rework"
APPROVAL_GATE_REVIEW_ACKNOWLEDGED: Final[str] = "approval_gate.review.acknowledged"
APPROVAL_GATE_RESUME_CONTEXT_LOADED: Final[str] = "approval_gate.resume.context_loaded"
APPROVAL_GATE_REVIEW_TRANSITION_FAILED: Final[str] = (
    "approval_gate.review.transition_failed"
)
APPROVAL_GATE_REVIEW_TRANSITION_SKIPPED: Final[str] = (
    "approval_gate.review.transition_skipped"
)
APPROVAL_GATE_REVIEW_STORE_RETRYING: Final[str] = "approval_gate.review.store_retrying"
APPROVAL_GATE_REVIEW_STORE_FAILED: Final[str] = "approval_gate.review.store_failed"
# The decision transition already succeeded (or, on the acknowledgement path,
# never applied); only its shielded audit/decision-record side effect did not
# land before a shutdown cancellation (a drain timeout, or the write failing on
# its own after the timeout). Kept distinct from TRANSITION_FAILED so a
# dashboard never reads this as a rejected transition.
APPROVAL_GATE_REVIEW_AUDIT_DRAIN_FAILED: Final[str] = (
    "approval_gate.review.audit_drain_failed"
)
# approval_gate.self_review.prevented and approval_gate.decision.recorded
# moved to events.security as SECURITY_APPROVAL_SELF_REVIEW_PREVENTED and
# SECURITY_APPROVAL_DECISION_RECORDED (audit-chained).
APPROVAL_GATE_DECISION_RECORD_FAILED: Final[str] = (
    "approval_gate.decision.record_failed"
)
# A decision offering options was decided without resolving one of them: no id
# supplied, or one naming no option on the package. The caller gets a 4xx, so
# without this the server side of a client contract bug leaves no trace.
APPROVAL_GATE_OPTION_UNRESOLVED: Final[str] = "approval_gate.decision.option_unresolved"
# One best-effort step of recording a decision into the project brain declined
# to run (not a decision fork, no brain wired, no resolvable project, ...), or
# its options metadata would not decode. Never fails the decision, so this is
# the only signal a shaping decision never reached the brain.
APPROVAL_GATE_BRAIN_RECORD_SKIPPED: Final[str] = "approval_gate.brain.record_skipped"
# Plan-review resume failure modes, kept distinct from the generic
# APPROVAL_GATE_RESUME_FAILED so an operator can tell a dispatch failure from a
# status-sync lag from a decision-record miss without parsing a free-text note.
APPROVAL_GATE_PLAN_DISPATCH_FAILED: Final[str] = "approval_gate.plan.dispatch_failed"
#: The approved plan's work items became persisted child tasks. Counted
#: because "the plan is EXECUTING" and "the plan has work" are different
#: facts, and only this one says the second.
APPROVAL_GATE_PLAN_CHILDREN_FILED: Final[str] = "approval_gate.plan.children_filed"
APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED: Final[str] = (
    "approval_gate.plan.status_sync_failed"
)
APPROVAL_GATE_PLAN_TASK_TRANSITION_FAILED: Final[str] = (
    "approval_gate.plan.task_transition_failed"
)
APPROVAL_GATE_PLAN_DECISION_RECORD_FAILED: Final[str] = (
    "approval_gate.plan.decision_record_failed"
)
# Resume-intent outbox: the crash-recovery marker written around the
# two-write approval decision, and the startup drain that finishes any
# resume the previous process died mid-way through. RECORD/CLEAR failures
# are non-fatal (the decision itself is unaffected), so they log at
# WARNING and are kept distinct from the drain's own outcomes.
APPROVAL_GATE_RESUME_INTENT_RECORD_FAILED: Final[str] = (
    "approval_gate.resume_intent.record_failed"
)
APPROVAL_GATE_RESUME_INTENT_CLEAR_FAILED: Final[str] = (
    "approval_gate.resume_intent.clear_failed"
)
APPROVAL_GATE_RESUME_INTENT_DRAIN_STARTED: Final[str] = (
    "approval_gate.resume_intent.drain_started"
)
APPROVAL_GATE_RESUME_INTENT_DRAIN_COMPLETED: Final[str] = (
    "approval_gate.resume_intent.drain_completed"
)
APPROVAL_GATE_RESUME_INTENT_REDISPATCHED: Final[str] = (
    "approval_gate.resume_intent.redispatched"
)
APPROVAL_GATE_RESUME_INTENT_DISCARDED: Final[str] = (
    "approval_gate.resume_intent.discarded"
)
APPROVAL_GATE_RESUME_INTENT_REDISPATCH_FAILED: Final[str] = (
    "approval_gate.resume_intent.redispatch_failed"
)
APPROVAL_GATE_TASK_NOT_FOUND: Final[str] = "approval_gate.task.not_found"
APPROVAL_GATE_TASK_UNASSIGNED: Final[str] = "approval_gate.task.unassigned"
APPROVAL_GATE_NOTIFICATION_FAILED: Final[str] = "approval_gate.notification.failed"

# Conversational steering-directive execution: an approved
# CONVERSATIONAL_INTAKE approval issues the directive; rejection is a
# deliberate no-op (see meta/chief_of_staff/_intake_parking.py).
APPROVAL_GATE_CONVERSATIONAL_EXECUTED: Final[str] = (
    "approval_gate.conversational.executed"
)

# Status-machine event: emitted on every ApprovalStatus hop --
# user-decided (PENDING -> APPROVED / REJECTED) in
# ``api/controllers/approvals.py``, and time-driven (PENDING ->
# EXPIRED) in the expiry sweep in ``api/approval_store.py``. Each
# emission fires AFTER the persistence write + cache update succeed
# so the audit stream only records hops that actually landed. The
# expiry path additionally emits ``API_APPROVAL_EXPIRED`` as a
# terminal-state summary event; the two are complementary
# (transition log = cross-hop audit, terminal event = "this is the
# final state" marker). Distinct from the audit-trail security
# events SECURITY_APPROVAL_APPROVED and SECURITY_APPROVAL_REJECTED
# so the state-transition log layer and the audit chain layer stay
# independent.
APPROVAL_STATUS_TRANSITIONED: Final[str] = "approval.status_transitioned"
