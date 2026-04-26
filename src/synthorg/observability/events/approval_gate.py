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
APPROVAL_GATE_RESUME_DELETE_FAILED: Final[str] = "approval_gate.resume.delete_failed"
APPROVAL_GATE_RESUME_TRIGGERED: Final[str] = "approval_gate.resume.triggered"
APPROVAL_GATE_NO_PARKED_CONTEXT: Final[str] = "approval_gate.no_parked_context"
APPROVAL_GATE_REVIEW_CREATED: Final[str] = "approval_gate.review.created"
APPROVAL_GATE_REVIEW_COMPLETED: Final[str] = "approval_gate.review.completed"
APPROVAL_GATE_REVIEW_REWORK: Final[str] = "approval_gate.review.rework"
APPROVAL_GATE_RESUME_CONTEXT_LOADED: Final[str] = "approval_gate.resume.context_loaded"
APPROVAL_GATE_REVIEW_TRANSITION_FAILED: Final[str] = (
    "approval_gate.review.transition_failed"
)
# approval_gate.self_review.prevented and approval_gate.decision.recorded
# moved to events.security as SECURITY_APPROVAL_SELF_REVIEW_PREVENTED and
# SECURITY_APPROVAL_DECISION_RECORDED (audit-chained).
APPROVAL_GATE_DECISION_RECORD_FAILED: Final[str] = (
    "approval_gate.decision.record_failed"
)
APPROVAL_GATE_TASK_NOT_FOUND: Final[str] = "approval_gate.task.not_found"
APPROVAL_GATE_TASK_UNASSIGNED: Final[str] = "approval_gate.task.unassigned"
APPROVAL_GATE_NOTIFICATION_FAILED: Final[str] = "approval_gate.notification.failed"
