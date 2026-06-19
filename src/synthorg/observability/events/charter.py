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
CHARTER_NOT_FOUND: Final[str] = "charter.not_found"
CHARTER_NOT_EDITABLE: Final[str] = "charter.not_editable"
CHARTER_ALREADY_DECIDED: Final[str] = "charter.already_decided"
CHARTER_CONVERSATION_NOT_FOUND: Final[str] = "charter.conversation.not_found"
CHARTER_CONVERSATION_CLOSED: Final[str] = "charter.conversation.closed"

# -- Approval to spine dispatch ----------------------------------------

CHARTER_APPROVED: Final[str] = "charter.approved"
CHARTER_CANCELLED: Final[str] = "charter.cancelled"
CHARTER_DISPATCHED: Final[str] = "charter.dispatched"
CHARTER_DISPATCH_FAILED: Final[str] = "charter.dispatch_failed"
CHARTER_PROJECT_ALREADY_EXISTS: Final[str] = "charter.project_already_exists"

# -- Data inconsistency ------------------------------------------------

CHARTER_STATE_INCONSISTENT: Final[str] = "charter.state_inconsistent"

# -- Boot wiring --------------------------------------------------------

CHARTER_SUBSTRATE_UNAVAILABLE: Final[str] = "charter.substrate.unavailable"

__all__ = [
    "CHARTER_ALREADY_DECIDED",
    "CHARTER_APPROVED",
    "CHARTER_CANCELLED",
    "CHARTER_CONVERSATION_CLOSED",
    "CHARTER_CONVERSATION_NOT_FOUND",
    "CHARTER_DISPATCHED",
    "CHARTER_DISPATCH_FAILED",
    "CHARTER_EDITED",
    "CHARTER_INTERVIEW_CAP_REACHED",
    "CHARTER_INTERVIEW_DRAFTED",
    "CHARTER_INTERVIEW_FAILED",
    "CHARTER_INTERVIEW_QUESTION",
    "CHARTER_INTERVIEW_RESPONSE_INVALID",
    "CHARTER_INTERVIEW_TURN",
    "CHARTER_NOT_EDITABLE",
    "CHARTER_NOT_FOUND",
    "CHARTER_OWNERSHIP_DENIED",
    "CHARTER_PROJECT_ALREADY_EXISTS",
    "CHARTER_STATE_INCONSISTENT",
    "CHARTER_STATUS_TRANSITIONED",
    "CHARTER_SUBSTRATE_UNAVAILABLE",
]
