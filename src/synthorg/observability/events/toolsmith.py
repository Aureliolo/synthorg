"""Event constants for the self-extending toolkit (toolsmith).

Constants follow the ``toolsmith.<area>.<action>`` naming convention and
are passed as the first argument to structured ``logger`` calls.
"""

from typing import Final

# Capability-gap detection
TOOLSMITH_GAP_RECORDED: Final[str] = "toolsmith.gap.recorded"
TOOLSMITH_GAP_RECORD_FAILED: Final[str] = "toolsmith.gap.record_failed"
TOOLSMITH_GAP_EVICTED: Final[str] = "toolsmith.gap.evicted"
TOOLSMITH_GAP_RECURRING_DETECTED: Final[str] = "toolsmith.gap.recurring_detected"

# Authoring
TOOLSMITH_AUTHOR_STARTED: Final[str] = "toolsmith.author.started"
TOOLSMITH_AUTHOR_COMPLETED: Final[str] = "toolsmith.author.completed"
TOOLSMITH_AUTHOR_FAILED: Final[str] = "toolsmith.author.failed"
TOOLSMITH_AUTHOR_SKIPPED: Final[str] = "toolsmith.author.skipped"
TOOLSMITH_AUTHOR_OVERFLOW_TO_CODE_MOD: Final[str] = (
    "toolsmith.author.overflow_to_code_mod"
)

# Proposal guard chain
TOOLSMITH_PROPOSAL_GUARD_REJECTED: Final[str] = "toolsmith.proposal.guard_rejected"

# Validation gate
TOOLSMITH_VALIDATION_STARTED: Final[str] = "toolsmith.validation.started"
TOOLSMITH_VALIDATION_PASSED: Final[str] = "toolsmith.validation.passed"
TOOLSMITH_VALIDATION_FAILED: Final[str] = "toolsmith.validation.failed"
TOOLSMITH_BRIEF_PARSE_FAILED: Final[str] = "toolsmith.validation.brief_parse_failed"

# Live registration
TOOLSMITH_TOOL_REGISTERED: Final[str] = "toolsmith.tool.registered"
TOOLSMITH_TOOL_UNREGISTERED: Final[str] = "toolsmith.tool.unregistered"
TOOLSMITH_TOOL_REGISTER_FAILED: Final[str] = "toolsmith.tool.register_failed"
TOOLSMITH_TOOL_INVOKED: Final[str] = "toolsmith.tool.invoked"
TOOLSMITH_TOOL_INVOKE_FAILED: Final[str] = "toolsmith.tool.invoke_failed"

# Applier lifecycle
TOOLSMITH_APPLY_STARTED: Final[str] = "toolsmith.apply.started"
TOOLSMITH_APPLY_COMPLETED: Final[str] = "toolsmith.apply.completed"
TOOLSMITH_APPLY_REJECTED: Final[str] = "toolsmith.apply.rejected"
TOOLSMITH_APPLY_FAILED: Final[str] = "toolsmith.apply.failed"
TOOLSMITH_BLUEPRINT_RETIRED: Final[str] = "toolsmith.blueprint.retired"

# Service wiring
TOOLSMITH_SERVICE_WIRED: Final[str] = "toolsmith.service.wired"
TOOLSMITH_SERVICE_UNAVAILABLE: Final[str] = "toolsmith.service.unavailable"
TOOLSMITH_CYCLE_STARTED: Final[str] = "toolsmith.cycle.started"
TOOLSMITH_CYCLE_COMPLETED: Final[str] = "toolsmith.cycle.completed"

# Autonomous cycle scheduler (periodic detect -> propose driver)
TOOLSMITH_CYCLE_SCHEDULER_STARTED: Final[str] = "toolsmith.cycle_scheduler.started"
TOOLSMITH_CYCLE_SCHEDULER_STOPPED: Final[str] = "toolsmith.cycle_scheduler.stopped"
TOOLSMITH_CYCLE_SCHEDULER_FAILED: Final[str] = "toolsmith.cycle_scheduler.failed"
TOOLSMITH_CYCLE_SCHEDULER_RAN: Final[str] = "toolsmith.cycle_scheduler.ran"
