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
