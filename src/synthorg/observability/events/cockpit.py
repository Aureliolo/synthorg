"""Cockpit event name constants for observability.

Covers the mission-control live activity feed, operator interventions,
and flight-recorder lifecycle. Format: ``cockpit.<noun>.<verb>`` /
``flight_recorder.<noun>.<verb>``.
"""

from typing import Final

COCKPIT_SNAPSHOT_PUBLISHED: Final[str] = "cockpit.snapshot.published"
COCKPIT_STUCK_DETECTED: Final[str] = "cockpit.stuck.detected"
COCKPIT_RUNAWAY_DETECTED: Final[str] = "cockpit.runaway.detected"
COCKPIT_INTERVENTION_INITIATED: Final[str] = "cockpit.intervention.initiated"
COCKPIT_INTERVENTION_APPLIED: Final[str] = "cockpit.intervention.applied"
COCKPIT_INTERVENTION_FAILED: Final[str] = "cockpit.intervention.failed"

STEERING_DIRECTIVE_ISSUED: Final[str] = "steering.directive.issued"
STEERING_DIRECTIVE_ADOPTED: Final[str] = "steering.directive.adopted"
STEERING_DIRECTIVE_SEEDED: Final[str] = "steering.directive.seeded"
STEERING_INBOX_READ_FAILED: Final[str] = "steering.inbox.read_failed"
STEERING_REPLAN_TRIGGERED: Final[str] = "steering.replan.triggered"
STEERING_SUPERSESSION_PROPOSED: Final[str] = "steering.supersession.proposed"
STEERING_PROPOSE_FAILED: Final[str] = "steering.supersession.propose_failed"
STEERING_TASKS_SUPERSEDED: Final[str] = "steering.tasks.superseded"
STEERING_TASK_SUPERSEDE_FAILED: Final[str] = "steering.task.supersede_failed"
STEERING_TASK_CANCELLED_OBSERVED: Final[str] = "steering.task.cancelled_observed"

FLIGHT_RECORDER_FRAME_RECORDED: Final[str] = "flight_recorder.frame.recorded"
FLIGHT_RECORDER_RECORD_FAILED: Final[str] = "flight_recorder.record.failed"
FLIGHT_RECORDER_QUEUE_OVERFLOW: Final[str] = "flight_recorder.queue.overflow"
FLIGHT_RECORDER_SEEK: Final[str] = "flight_recorder.seek"
FLIGHT_RECORDER_PURGE: Final[str] = "flight_recorder.purge"
