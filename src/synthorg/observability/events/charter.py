"""Project-charter event constants for structured logging.

Constants follow the ``charter.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls. Covers
the interview loop, the review/edit lifecycle, the approval-to-spine
dispatch, and boot wiring.
"""

from typing import Final

# -- Interview loop -----------------------------------------------------

CHARTER_INTERVIEW_TURN: Final[str] = "charter.interview.turn"
CHARTER_INTERVIEW_QUESTION: Final[str] = "charter.interview.question"
CHARTER_INTERVIEW_DRAFTED: Final[str] = "charter.interview.drafted"
CHARTER_INTERVIEW_CAP_REACHED: Final[str] = "charter.interview.cap_reached"
CHARTER_INTERVIEW_RESPONSE_INVALID: Final[str] = "charter.interview.response_invalid"
CHARTER_INTERVIEW_FAILED: Final[str] = "charter.interview.failed"

# -- Review / edit lifecycle -------------------------------------------

CHARTER_EDITED: Final[str] = "charter.edited"
CHARTER_STATUS_TRANSITIONED: Final[str] = "charter.status_transitioned"
CHARTER_OWNERSHIP_DENIED: Final[str] = "charter.ownership_denied"

# -- Approval to spine dispatch ----------------------------------------

CHARTER_APPROVED: Final[str] = "charter.approved"
CHARTER_CANCELLED: Final[str] = "charter.cancelled"
CHARTER_DISPATCHED: Final[str] = "charter.dispatched"
CHARTER_DISPATCH_FAILED: Final[str] = "charter.dispatch_failed"

# -- Boot wiring --------------------------------------------------------

CHARTER_SUBSTRATE_UNAVAILABLE: Final[str] = "charter.substrate.unavailable"

__all__ = [
    "CHARTER_APPROVED",
    "CHARTER_CANCELLED",
    "CHARTER_DISPATCHED",
    "CHARTER_DISPATCH_FAILED",
    "CHARTER_EDITED",
    "CHARTER_INTERVIEW_CAP_REACHED",
    "CHARTER_INTERVIEW_DRAFTED",
    "CHARTER_INTERVIEW_FAILED",
    "CHARTER_INTERVIEW_QUESTION",
    "CHARTER_INTERVIEW_RESPONSE_INVALID",
    "CHARTER_INTERVIEW_TURN",
    "CHARTER_OWNERSHIP_DENIED",
    "CHARTER_STATUS_TRANSITIONED",
    "CHARTER_SUBSTRATE_UNAVAILABLE",
]
