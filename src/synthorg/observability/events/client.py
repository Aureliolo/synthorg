"""Client simulation event constants."""

from typing import Final

CLIENT_REQUEST_SUBMITTED: Final[str] = "client.request.submitted"
CLIENT_REQUEST_TRIAGING: Final[str] = "client.request.triaging"
CLIENT_REQUEST_SCOPED: Final[str] = "client.request.scoped"
CLIENT_REQUEST_APPROVED: Final[str] = "client.request.approved"
CLIENT_REQUEST_REJECTED: Final[str] = "client.request.rejected"
CLIENT_REQUEST_INTAKE_PIPELINE_FAILED: Final[str] = (
    "client.request.intake_pipeline_failed"
)
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
# Rejected start attempt (already running) -- kept distinct from
# CONTINUOUS_MODE_STARTED so a refused duplicate start does not inflate
# successful-start counts on dashboards.
CONTINUOUS_MODE_START_REJECTED: Final[str] = "continuous.mode.start_rejected"
CONTINUOUS_MODE_STOPPED: Final[str] = "continuous.mode.stopped"
CLIENT_FEEDBACK_SINK_FAILED: Final[str] = "client.feedback.sink_failed"

# Factory dispatch events -------------------------------------------------

CLIENT_FACTORY_UNKNOWN_STRATEGY: Final[str] = "client.factory.unknown_strategy"

# Client-simulation runtime boot wiring (emitted by the builder that
# constructs the IntakeEngine + ReviewPipeline at app construction).
CLIENT_SIMULATION_RUNTIME_WIRED: Final[str] = "client.simulation.runtime_wired"

CLIENT_REQUEST_TRANSITION: Final[str] = "client.request.transition"
CLIENT_REQUEST_TRANSITION_INVALID: Final[str] = "client.request.transition_invalid"
CLIENT_REQUEST_TRANSITION_CONFIG_ERROR: Final[str] = (
    "client.request.transition_config_error"
)

CLIENT_CONFIG_INVALID: Final[str] = "client.config.invalid"

# Pool / store lookup failures emitted before the corresponding KeyError
# so operators see which CRUD operation rejected a missing identifier,
# not just a bare exception in the controller's error envelope.
CLIENT_NOT_FOUND: Final[str] = "client.pool.client_not_found"
CLIENT_REQUEST_NOT_FOUND: Final[str] = "client.request.not_found"
SIMULATION_RUN_NOT_FOUND: Final[str] = "simulation.run.not_found"

# Persisted status transition (emitted at the caller AFTER the request
# is saved). Pure-constructor transitions inside ``with_status`` are
# covered by CLIENT_REQUEST_TRANSITION; this event records "the row in
# the store now carries the new status".
CLIENT_REQUEST_STATUS_TRANSITIONED: Final[str] = "client.request.status_transitioned"
