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
STEERING_DIRECTIVE_REJECTED: Final[str] = "steering.directive.rejected"
STEERING_INBOX_READ_FAILED: Final[str] = "steering.inbox.read_failed"
STEERING_SUPERSESSION_PROPOSED: Final[str] = "steering.supersession.proposed"
STEERING_PROPOSE_FAILED: Final[str] = "steering.supersession.propose_failed"
STEERING_TASKS_SUPERSEDED: Final[str] = "steering.tasks.superseded"
STEERING_TASK_SUPERSEDE_FAILED: Final[str] = "steering.task.supersede_failed"
STEERING_TASK_SCOPE_REJECTED: Final[str] = "steering.task.scope_rejected"
STEERING_TASK_CANCELLED_OBSERVED: Final[str] = "steering.task.cancelled_observed"

FLIGHT_RECORDER_FRAME_RECORDED: Final[str] = "flight_recorder.frame.recorded"
FLIGHT_RECORDER_RECORD_FAILED: Final[str] = "flight_recorder.record.failed"
FLIGHT_RECORDER_QUEUE_OVERFLOW: Final[str] = "flight_recorder.queue.overflow"
FLIGHT_RECORDER_SEEK: Final[str] = "flight_recorder.seek"
FLIGHT_RECORDER_PURGE: Final[str] = "flight_recorder.purge"

AGENT_RUNTIME_STATE_WRITE_FAILED: Final[str] = "agent_runtime_state.write.failed"
"""The live per-agent runtime state could not be persisted. Recording it is
observation, so the failure is logged rather than raised into the run: the
consequence is that the live view falls back to the recorded frames for that
agent, which is what it did for every agent before anything wrote this row."""

AGENT_RUNTIME_STATE_IDLE_SKIPPED: Final[str] = "agent_runtime_state.idle.skipped"
"""A dispatch finished while the live row named a sibling dispatch on the same
agent, so the idle clear was skipped rather than blanking the sibling's row and
reporting a working agent as idle."""

TURN_OBSERVER_FAILED: Final[str] = "turn_observer.failed"
"""One per-turn observer raised. The others still ran and the run continued:
watching a run must never fail it, and one watcher's fault must not blind the
rest."""
