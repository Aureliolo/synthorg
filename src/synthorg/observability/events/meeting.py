"""Meeting protocol event constants."""

from typing import Final

# Meeting lifecycle
MEETING_STARTED: Final[str] = "meeting.lifecycle.started"
MEETING_COMPLETED: Final[str] = "meeting.lifecycle.completed"
MEETING_FAILED: Final[str] = "meeting.lifecycle.failed"
MEETING_CANCELLED: Final[str] = "meeting.lifecycle.cancelled"
"""Operator-triggered cancellation -- e.g. the
``communication.meetings_enabled`` kill switch is engaged.  Kept distinct
from ``MEETING_FAILED`` so an intentional pause does not skew failure
metrics or trigger alerts wired off ``meeting.lifecycle.failed``."""
MEETING_BUDGET_EXHAUSTED: Final[str] = "meeting.lifecycle.budget_exhausted"

# Phase tracking
MEETING_PHASE_STARTED: Final[str] = "meeting.phase.started"
MEETING_PHASE_COMPLETED: Final[str] = "meeting.phase.completed"

# Agent interaction
MEETING_AGENT_CALLED: Final[str] = "meeting.agent.called"
MEETING_AGENT_RESPONDED: Final[str] = "meeting.agent.responded"
MEETING_AGENT_CALL_FAILED: Final[str] = "meeting.agent.call_failed"
MEETING_CONTRIBUTION_RECORDED: Final[str] = "meeting.contribution.recorded"

# Conflict detection
MEETING_CONFLICT_DETECTED: Final[str] = "meeting.conflict.detected"

# Conflict escalation (post-meeting bridge into the conflict-resolution service)
MEETING_CONFLICT_ESCALATION_STARTED: Final[str] = "meeting.conflict.escalation.started"
MEETING_CONFLICT_ESCALATION_RESOLVED: Final[str] = (
    "meeting.conflict.escalation.resolved"
)
MEETING_CONFLICT_ESCALATION_SKIPPED: Final[str] = "meeting.conflict.escalation.skipped"
MEETING_CONFLICT_ESCALATION_FAILED: Final[str] = "meeting.conflict.escalation.failed"

# Strategy dispatch (premortem / consensus-velocity)
MEETING_CONSENSUS_VELOCITY_FORCED: Final[str] = "meeting.consensus_velocity.forced"
"""Premature-consensus detection on the gathered input positions forced
the discussion (devil's-advocate) round to run even though the leader's
conflict check found none."""
MEETING_PREMORTEM_APPENDED: Final[str] = "meeting.premortem.appended"
"""A premortem analysis section was folded into the synthesis summary."""
MEETING_CONSENSUS_VELOCITY_FAILED: Final[str] = "meeting.consensus_velocity.failed"
"""The injected consensus-velocity hook raised; the advisory check is
skipped (discussion not force-forced) rather than aborting the meeting."""
MEETING_PREMORTEM_FAILED: Final[str] = "meeting.premortem.failed"
"""The injected premortem hook raised; the synthesis summary is returned
without a premortem section rather than aborting the meeting."""

# Output generation
MEETING_SUMMARY_GENERATED: Final[str] = "meeting.summary.generated"
MEETING_ACTION_ITEM_EXTRACTED: Final[str] = "meeting.action_item.extracted"

# Task creation from action items
MEETING_TASK_CREATED: Final[str] = "meeting.task.created"
MEETING_TASK_CREATION_FAILED: Final[str] = "meeting.task.creation_failed"

# Validation and resolution
MEETING_VALIDATION_FAILED: Final[str] = "meeting.validation.failed"
MEETING_PROTOCOL_NOT_FOUND: Final[str] = "meeting.protocol.not_found"

# Phase skipping
MEETING_SYNTHESIS_SKIPPED: Final[str] = "meeting.synthesis.skipped"
MEETING_SUMMARY_SKIPPED: Final[str] = "meeting.summary.skipped"

# Token tracking
MEETING_TOKENS_RECORDED: Final[str] = "meeting.tokens.recorded"

# Parsing
MEETING_PARSING_NO_SECTION: Final[str] = "meeting.parsing.no_section"

# Internal invariant violations
MEETING_INTERNAL_ERROR: Final[str] = "meeting.internal.error"
MEETING_RECORD_MIRROR_DRIFT: Final[str] = "meeting.internal.record_mirror_drift"
"""Storage invariant: ``_records_by_id`` and ``_records`` diverged.

Distinct from ``MEETING_FAILED`` because this is a delete-time
storage-mirror bug (the dict did not see a record present in the
list), not a meeting-execution failure. Dashboards / alerts wired off
``meeting.lifecycle.failed`` would otherwise misclassify these as
real protocol failures and inflate the failure-rate signal."""

# Scheduler lifecycle
MEETING_SCHEDULER_STARTED: Final[str] = "meeting.scheduler.started"
MEETING_SCHEDULER_STOPPED: Final[str] = "meeting.scheduler.stopped"
MEETING_PERIODIC_TRIGGERED: Final[str] = "meeting.scheduler.periodic_triggered"
MEETING_EVENT_TRIGGERED: Final[str] = "meeting.scheduler.event_triggered"
MEETING_PARTICIPANTS_RESOLVED: Final[str] = "meeting.scheduler.participants_resolved"
MEETING_NO_PARTICIPANTS: Final[str] = "meeting.scheduler.no_participants"
MEETING_SCHEDULER_ERROR: Final[str] = "meeting.scheduler.error"
MEETING_EVENT_COOLDOWN_SKIPPED: Final[str] = "meeting.scheduler.event_cooldown_skipped"
MEETING_SCHEDULER_TASK_DIED: Final[str] = "meeting.scheduler.task_died"

# Task capping
MEETING_TASKS_CAPPED: Final[str] = "meeting.task.capped"

# Strategy integration
MEETING_LENS_ASSIGNMENT_FAILED: Final[str] = "meeting.strategy.lens_assignment_failed"
MEETING_BUDGET_SCALED: Final[str] = "meeting.strategy.budget_scaled"
"""The progressive-tier budget scaler adjusted a meeting type's static
``duration_tokens`` to a tier-resolved token budget."""

# API-level meeting events
MEETING_NOT_FOUND: Final[str] = "meeting.api.not_found"
