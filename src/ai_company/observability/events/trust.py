"""Trust event constants for structured logging.

Constants follow the ``trust.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

TRUST_EVALUATE_START: Final[str] = "trust.evaluate.start"
TRUST_EVALUATE_COMPLETE: Final[str] = "trust.evaluate.complete"
TRUST_EVALUATE_FAILED: Final[str] = "trust.evaluate.failed"
TRUST_LEVEL_CHANGED: Final[str] = "trust.level.changed"
TRUST_APPROVAL_REQUIRED: Final[str] = "trust.approval.required"
TRUST_APPROVAL_STORE_MISSING: Final[str] = "trust.approval.store_missing"
TRUST_DECAY_DETECTED: Final[str] = "trust.decay.detected"
TRUST_INITIALIZED: Final[str] = "trust.agent.initialized"
TRUST_ELEVATED_GATE_ENFORCED: Final[str] = "trust.elevated.gate_enforced"
