"""Meeting protocol event constants."""

from typing import Final

# Meeting lifecycle
MEETING_STARTED: Final[str] = "meeting.lifecycle.started"
MEETING_COMPLETED: Final[str] = "meeting.lifecycle.completed"
MEETING_FAILED: Final[str] = "meeting.lifecycle.failed"
MEETING_BUDGET_EXHAUSTED: Final[str] = "meeting.lifecycle.budget_exhausted"

# Phase tracking
MEETING_PHASE_STARTED: Final[str] = "meeting.phase.started"
MEETING_PHASE_COMPLETED: Final[str] = "meeting.phase.completed"

# Agent interaction
MEETING_AGENT_CALLED: Final[str] = "meeting.agent.called"
MEETING_AGENT_RESPONDED: Final[str] = "meeting.agent.responded"
MEETING_CONTRIBUTION_RECORDED: Final[str] = "meeting.contribution.recorded"

# Conflict detection
MEETING_CONFLICT_DETECTED: Final[str] = "meeting.conflict.detected"

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

# Internal invariant violations
MEETING_INTERNAL_ERROR: Final[str] = "meeting.internal.error"
