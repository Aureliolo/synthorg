"""Client simulation event constants."""

from typing import Final

CLIENT_REQUEST_SUBMITTED: Final[str] = "client.request.submitted"
CLIENT_REQUEST_TRIAGING: Final[str] = "client.request.triaging"
CLIENT_REQUEST_SCOPED: Final[str] = "client.request.scoped"
CLIENT_REQUEST_APPROVED: Final[str] = "client.request.approved"
CLIENT_REQUEST_REJECTED: Final[str] = "client.request.rejected"
CLIENT_REVIEW_STARTED: Final[str] = "client.review.started"
CLIENT_REVIEW_COMPLETED: Final[str] = "client.review.completed"
CLIENT_FEEDBACK_RECORDED: Final[str] = "client.feedback.recorded"
CLIENT_REQUIREMENT_GENERATED: Final[str] = "client.requirement.generated"
SIMULATION_RUN_STARTED: Final[str] = "simulation.run.started"
SIMULATION_RUN_COMPLETED: Final[str] = "simulation.run.completed"
SIMULATION_RUN_FAILED: Final[str] = "simulation.run.failed"
SIMULATION_RUN_CANCELLED: Final[str] = "simulation.run.cancelled"
# Invalid update attempts (pre-transition) -- kept distinct from the
# terminal SIMULATION_RUN_FAILED so sinks and dashboards can filter
# "actual run failures" vs "rejected/invalid writes".
SIMULATION_RUN_UPDATE_REJECTED: Final[str] = "simulation.run.update_rejected"
SIMULATION_ROUND_COMPLETED: Final[str] = "simulation.round.completed"
CONTINUOUS_MODE_DISABLED: Final[str] = "continuous.mode.disabled"
CONTINUOUS_MODE_STARTED: Final[str] = "continuous.mode.started"
CONTINUOUS_MODE_STOPPED: Final[str] = "continuous.mode.stopped"
CLIENT_FEEDBACK_SINK_FAILED: Final[str] = "client.feedback.sink_failed"

# Factory dispatch events -------------------------------------------------

CLIENT_FACTORY_UNKNOWN_STRATEGY: Final[str] = "client.factory.unknown_strategy"

CLIENT_REQUEST_TRANSITION: Final[str] = "client.request.transition"
CLIENT_REQUEST_TRANSITION_INVALID: Final[str] = "client.request.transition_invalid"
CLIENT_REQUEST_TRANSITION_CONFIG_ERROR: Final[str] = (
    "client.request.transition_config_error"
)

CLIENT_CONFIG_INVALID: Final[str] = "client.config.invalid"
