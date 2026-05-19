"""Chief of Staff event constants for structured logging.

Constants follow the ``chief_of_staff.<subject>.<action>`` naming
convention and are passed as the first argument to structured log calls.
"""

from typing import Final

# -- Outcome recording --------------------------------------------------

COS_OUTCOME_RECORDED: Final[str] = "chief_of_staff.outcome.recorded"
COS_OUTCOME_RECORD_FAILED: Final[str] = "chief_of_staff.outcome.record_failed"
COS_OUTCOME_SKIPPED: Final[str] = "chief_of_staff.outcome.skipped"

# -- Confidence adjustment ----------------------------------------------

COS_CONFIDENCE_ADJUSTED: Final[str] = "chief_of_staff.confidence.adjusted"
COS_CONFIDENCE_ADJUSTMENT_FAILED: Final[str] = (
    "chief_of_staff.confidence.adjustment_failed"
)
COS_CONFIDENCE_NO_HISTORY: Final[str] = "chief_of_staff.confidence.no_history"

# -- Learning lifecycle -------------------------------------------------

COS_LEARNING_ENABLED: Final[str] = "chief_of_staff.learning.enabled"

# -- Org inflection detection -------------------------------------------

COS_INFLECTION_DETECTED: Final[str] = "chief_of_staff.inflection.detected"
COS_INFLECTION_CHECK_FAILED: Final[str] = "chief_of_staff.inflection.check_failed"

# -- Proactive alerts ---------------------------------------------------

COS_ALERT_EMITTED: Final[str] = "chief_of_staff.alert.emitted"
COS_ALERT_SUPPRESSED: Final[str] = "chief_of_staff.alert.suppressed"
COS_MONITOR_STARTED: Final[str] = "chief_of_staff.monitor.started"
COS_MONITOR_STOPPED: Final[str] = "chief_of_staff.monitor.stopped"
COS_MONITOR_LOOP_DIED: Final[str] = "chief_of_staff.monitor.loop_died"

# -- Chat ---------------------------------------------------------------

COS_CHAT_QUERY: Final[str] = "chief_of_staff.chat.query"
COS_CHAT_RESPONSE: Final[str] = "chief_of_staff.chat.response"
COS_CHAT_FAILED: Final[str] = "chief_of_staff.chat.failed"

# -- Clarify + propose -------------------------------------------------

COS_PROPOSE_TURN: Final[str] = "chief_of_staff.propose.turn"
COS_PROPOSE_CLARIFICATION: Final[str] = "chief_of_staff.propose.clarification"
COS_PROPOSE_PROPOSED: Final[str] = "chief_of_staff.propose.proposed"
COS_PROPOSE_CAP_REACHED: Final[str] = "chief_of_staff.propose.cap_reached"
COS_PROPOSE_RESPONSE_INVALID: Final[str] = "chief_of_staff.propose.response_invalid"
COS_PROPOSE_FAILED: Final[str] = "chief_of_staff.propose.failed"
