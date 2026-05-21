"""Event-name constants for the vision verifier subsystem.

Centralised so structured logs use stable identifiers; never inline
literal event strings in verifier code.
"""

from typing import Final

VISION_GATE_STARTED: Final[str] = "vision_verify.gate.started"
VISION_GATE_PASSED: Final[str] = "vision_verify.gate.passed"
VISION_GATE_BLOCKED: Final[str] = "vision_verify.gate.blocked"
VISION_GATE_SKIPPED: Final[str] = "vision_verify.gate.skipped"
VISION_REWORK_ROUTED: Final[str] = "vision_verify.rework.routed"

VISION_VERIFIER_INVOKED: Final[str] = "vision_verify.verifier.invoked"
VISION_VERIFIER_FAILED: Final[str] = "vision_verify.verifier.failed"

VISION_HEURISTIC_CHECK_COMPLETED: Final[str] = "vision_verify.heuristic.completed"

VISION_LLM_CALL_STARTED: Final[str] = "vision_verify.llm.started"
VISION_LLM_CALL_COMPLETED: Final[str] = "vision_verify.llm.completed"
VISION_LLM_UNSUPPORTED: Final[str] = "vision_verify.llm.unsupported"
