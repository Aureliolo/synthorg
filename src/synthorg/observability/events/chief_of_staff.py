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
COS_CONVERSATION_STATUS_TRANSITIONED: Final[str] = (
    "chief_of_staff.conversation.status_transitioned"
)

# -- Concern routing ---------------------------------------------------

COS_ROUTING_ROUTED: Final[str] = "chief_of_staff.routing.routed"
COS_ROUTING_FALLBACK: Final[str] = "chief_of_staff.routing.fallback"
COS_ROUTING_RESPONSE_INVALID: Final[str] = "chief_of_staff.routing.response_invalid"

# -- Multi-agent group chat --------------------------------------------

COS_GROUP_ROUND_STARTED: Final[str] = "chief_of_staff.group_chat.round_started"
COS_GROUP_CONTRIBUTION: Final[str] = "chief_of_staff.group_chat.contribution"
COS_GROUP_CONTRIBUTION_FAILED: Final[str] = (
    "chief_of_staff.group_chat.contribution_failed"
)
COS_GROUP_ROUND_COMPLETED: Final[str] = "chief_of_staff.group_chat.round_completed"
COS_GROUP_ROUND_TRUNCATED: Final[str] = "chief_of_staff.group_chat.round_truncated"
COS_GROUP_AUTHORITY_CUES_DETECTED: Final[str] = (
    "chief_of_staff.group_chat.authority_cues_detected"
)
COS_GROUP_PARTICIPANTS_ADDED: Final[str] = (
    "chief_of_staff.group_chat.participants_added"
)
# Participant-roster repo events. Read/query markers + failure path only;
# the persistence boundary forbids repos from emitting mutation lifecycle
# events (the GroupChatService owns the membership audit hop).
COS_GROUP_PARTICIPANT_FETCHED: Final[str] = (
    "chief_of_staff.group_chat.participant_fetched"
)
COS_GROUP_PARTICIPANT_LISTED: Final[str] = (
    "chief_of_staff.group_chat.participant_listed"
)
COS_GROUP_PARTICIPANT_FAILED: Final[str] = (
    "chief_of_staff.group_chat.participant_failed"
)
# Agent-invite repo events. Read/query markers + failure path
# only; the persistence boundary forbids repos from emitting mutation
# lifecycle events (the invite park / consent flows own that audit hop).
COS_GROUP_INVITE_FETCHED: Final[str] = "chief_of_staff.group_chat.invite_fetched"
COS_GROUP_INVITE_LISTED: Final[str] = "chief_of_staff.group_chat.invite_listed"
COS_GROUP_INVITE_FAILED: Final[str] = "chief_of_staff.group_chat.invite_failed"
# Agent-invite lifecycle events: park (consent requested),
# skip (a bound tripped), malformed structured response, and the
# consent-resume outcomes.
COS_GROUP_INVITE_REQUESTED: Final[str] = "chief_of_staff.group_chat.invite_requested"
COS_GROUP_INVITE_SKIPPED: Final[str] = "chief_of_staff.group_chat.invite_skipped"
COS_GROUP_INVITE_RESPONSE_INVALID: Final[str] = (
    "chief_of_staff.group_chat.invite_response_invalid"
)
COS_GROUP_INVITE_PARK_FAILED: Final[str] = (
    "chief_of_staff.group_chat.invite_park_failed"
)
COS_GROUP_INVITE_ACCEPTED: Final[str] = "chief_of_staff.group_chat.invite_accepted"
COS_GROUP_INVITE_DECLINED: Final[str] = "chief_of_staff.group_chat.invite_declined"

# -- Direct MCP acting under trust -------------------------------------

COS_ACT_REQUESTED: Final[str] = "chief_of_staff.act.requested"
COS_ACT_COMPLETED: Final[str] = "chief_of_staff.act.completed"
COS_ACT_PARKED: Final[str] = "chief_of_staff.act.parked"
COS_ACT_FAILED: Final[str] = "chief_of_staff.act.failed"

# -- Run narrative (documentary mode) ----------------------------------

COS_NARRATIVE_GENERATION_STARTED: Final[str] = (
    "chief_of_staff.narrative.generation_started"
)
COS_NARRATIVE_GENERATED: Final[str] = "chief_of_staff.narrative.generated"
COS_NARRATIVE_GENERATION_FAILED: Final[str] = (
    "chief_of_staff.narrative.generation_failed"
)
COS_NARRATIVE_SOURCE_UNAVAILABLE: Final[str] = (
    "chief_of_staff.narrative.source_unavailable"
)
COS_NARRATIVE_SKIPPED: Final[str] = "chief_of_staff.narrative.skipped"
COS_NARRATIVE_PROSE_FALLBACK: Final[str] = "chief_of_staff.narrative.prose_fallback"
