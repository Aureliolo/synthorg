"""Workflow event name constants for observability.

Covers both Kanban board and Agile sprint workflow types.
"""

# -- Kanban events ----------------------------------------------------------

KANBAN_COLUMN_TRANSITION: str = "workflow.kanban.column_transition"
"""Task moved between Kanban columns."""

KANBAN_WIP_LIMIT_REACHED: str = "workflow.kanban.wip_limit_reached"
"""Column WIP count equals the configured limit."""

KANBAN_WIP_LIMIT_EXCEEDED: str = "workflow.kanban.wip_limit_exceeded"
"""Column WIP count exceeds the configured limit."""

KANBAN_COLUMN_TRANSITION_INVALID: str = "workflow.kanban.column_transition_invalid"
"""Invalid Kanban column transition attempted."""

KANBAN_COLUMN_TRANSITION_CONFIG_ERROR: str = (
    "workflow.kanban.column_transition_config_error"
)
"""Kanban transition table is stale versus the enum -- configuration bug."""

KANBAN_STATUS_PATH_MISSING: str = "workflow.kanban.status_path_missing"
"""No task status path defined for a column move."""

WORKFLOW_CONFIG_UNUSED_SUBCONFIG: str = "workflow.config.unused_subconfig"
"""Sub-config customized for an inactive workflow type (advisory)."""

KANBAN_CONFIG_VALIDATION_FAILED: str = "workflow.kanban.config_validation_failed"
"""Kanban configuration validation failed."""

KANBAN_TASK_PLACED: str = "workflow.kanban.task_placed"
"""Task placed on the Kanban board (initial column assignment)."""

# -- Sprint events ----------------------------------------------------------

SPRINT_CREATED: str = "workflow.sprint.created"
"""New sprint created."""

SPRINT_SERVICE_OBSERVER_FAILED: str = "workflow.sprint.service_observer_failed"
"""SprintService task-state observer swallowed a non-critical error."""

SPRINT_GATE_BLOCKED: str = "workflow.sprint.gate_blocked"
"""Sprint gate rejected working a task outside the active sprint backlog."""

SPRINT_GATE_CHECK_FAILED: str = "workflow.sprint.gate_check_failed"
"""The advisory sprint gate could not read sprint state (e.g. store outage);
the move is allowed through rather than blocked, keeping the gate advisory."""

SPRINT_TRANSITION_LOST: str = "workflow.sprint.transition_lost"
"""A lifecycle CAS returned False: the expected from-state was gone, so the
attempted transition did not persist (a concurrent advance, a duplicate
completion event, or corruption). The in-memory transition is discarded."""

SPRINT_LIFECYCLE_TRANSITION: str = "workflow.sprint.lifecycle_transition"
"""Sprint transitioned between lifecycle statuses."""

SPRINT_STATUS_TRANSITIONED: str = "workflow.sprint.status_transitioned"
"""Sprint row in the store now carries the new status (emitted by the
caller AFTER persistence write). ``with_transition`` is a pure
constructor so the SPRINT_LIFECYCLE_TRANSITION event from the state
machine covers transition *intent*; this event records the persisted
state-of-record."""

SPRINT_LIFECYCLE_TRANSITION_INVALID: str = (
    "workflow.sprint.lifecycle_transition_invalid"
)
"""Invalid sprint lifecycle transition attempted."""

SPRINT_LIFECYCLE_TRANSITION_CONFIG_ERROR: str = (
    "workflow.sprint.lifecycle_transition_config_error"
)
"""Sprint transition table is stale versus the enum -- configuration bug."""

SPRINT_TASK_ADDED: str = "workflow.sprint.task_added"
"""Task added to sprint backlog."""

SPRINT_TASK_REMOVED: str = "workflow.sprint.task_removed"
"""Task removed from sprint backlog."""

SPRINT_TASK_COMPLETED: str = "workflow.sprint.task_completed"
"""Task marked completed within a sprint."""

SPRINT_BACKLOG_SAVE_FAILED: str = "workflow.sprint.backlog_save_failed"
"""Source-of-truth sprint backlog write failed; the sprint's completed-task
set has diverged from actual task state and needs operator reconciliation
(the task's own status is authoritative and unaffected)."""

SPRINT_BACKLOG_SAVE_RETRYING: str = "workflow.sprint.backlog_save_retrying"
"""A sprint backlog write failed transiently and is being retried. One line
per attempt, so a store that is merely slow is distinguishable from one that
is down before SPRINT_BACKLOG_SAVE_FAILED is reached."""

SPRINT_COMPLETION_ALREADY_RECORDED: str = "workflow.sprint.completion_already_recorded"
"""The guarded append matched nothing because another writer had already
recorded this delivery. Not a failure: this process re-read the row and
drives the tail from it, because if both writers deferred to the other,
neither would."""

SPRINT_COMPLETION_NOT_RECORDED: str = "workflow.sprint.completion_not_recorded"
"""The guarded append matched nothing and the re-read does not show the task
as delivered either, so this completion was dropped. The sprint's status left
the window a completion is admissible in, or its row went, between the read
that chose the sprint and the write."""

SPRINT_TAIL_NOT_SPAWNED: str = "workflow.sprint.tail_not_spawned"
"""A lifecycle tail walk was not started because the service is draining.
Not a loss: the walk is derived from the row's own delivery state, so the
recovery sweep re-asks for it on its next pass."""

SPRINT_TAIL_DRAIN_TIMED_OUT: str = "workflow.sprint.tail_drain_timed_out"
"""The shutdown drain hit its deadline with tail walks still in flight. Each
hop is a compare-and-set, so nothing is corrupt; the sprint is left partway
along its tail for the next process's boot recovery pass."""

SPRINT_REFUSED: str = "workflow.sprint.refused"
"""A sprint operation was refused and the caller told why. Carries the
``reason`` the error names, so a refusal an operator reports is findable in
the log without reproducing it."""

SPRINT_CREATE_RACE_LOST: str = "workflow.sprint.create_race_lost"
"""Another writer opened this scope's sprint first, so the partial unique
index refused this insert. Not a failure: the scope has the sprint it needs
and this process joins it on the next event."""

SPRINT_RESUMED: str = "workflow.sprint.resumed"
"""The recovery sweep moved a sprint nothing else was left to move: a
lifecycle hop whose triggering event was lost with the process that was
handling it."""

SPRINT_TAIL_SWEEP_STARTED: str = "workflow.sprint.tail_sweep_started"
"""A sprint recovery pass began (boot or periodic)."""

SPRINT_TAIL_SWEEP_COMPLETE: str = "workflow.sprint.tail_sweep_complete"
"""A sprint recovery pass finished, with what it found and did."""

SPRINT_TAIL_SWEEP_FAILED: str = "workflow.sprint.tail_sweep_failed"
"""A sprint recovery pass could not read or advance a sprint. One unreadable
sprint never stops the pass; the rest still need picking up."""

SPRINT_TAIL_SWEEP_PAUSED: str = "workflow.sprint.tail_sweep_paused"
"""An operator has paused the sprint recovery sweep; this tick did nothing."""

SPRINT_TAIL_SCHEDULER_STARTED: str = "workflow.sprint.tail_scheduler_started"
"""The sprint recovery scheduler's background loop started."""

SPRINT_TAIL_SCHEDULER_STOPPED: str = "workflow.sprint.tail_scheduler_stopped"
"""The sprint recovery scheduler's background loop stopped cleanly."""

SPRINT_TAIL_SCHEDULER_FAILED: str = "workflow.sprint.tail_scheduler_failed"
"""The sprint recovery scheduler's loop or lifecycle failed."""

SPRINT_BACKLOG_INVALID: str = "workflow.sprint.backlog_invalid"
"""Invalid sprint backlog operation attempted."""

SPRINT_VELOCITY_INVALID: str = "workflow.sprint.velocity_invalid"
"""Invalid velocity operation attempted."""

SPRINT_VELOCITY_RECORDED: str = "workflow.sprint.velocity_recorded"
"""Velocity record created from a completed sprint."""
