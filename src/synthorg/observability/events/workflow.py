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

SPRINT_BACKLOG_INVALID: str = "workflow.sprint.backlog_invalid"
"""Invalid sprint backlog operation attempted."""

SPRINT_VELOCITY_INVALID: str = "workflow.sprint.velocity_invalid"
"""Invalid velocity operation attempted."""

SPRINT_VELOCITY_RECORDED: str = "workflow.sprint.velocity_recorded"
"""Velocity record created from a completed sprint."""

SPRINT_CEREMONY_SCHEDULED: str = "workflow.sprint.ceremony_scheduled"
"""Sprint ceremony scheduled."""

SPRINT_CEREMONY_TRIGGERED: str = "workflow.sprint.ceremony_triggered"
"""Sprint ceremony triggered by strategy evaluation."""

SPRINT_CEREMONY_SKIPPED: str = "workflow.sprint.ceremony_skipped"
"""Ceremony evaluation returned false -- ceremony not fired."""

SPRINT_AUTO_TRANSITION: str = "workflow.sprint.auto_transition"
"""Sprint auto-transitioned by ceremony scheduling strategy."""

SPRINT_STRATEGY_CONFIG_INVALID: str = "workflow.sprint.strategy_config_invalid"
"""Strategy config validation failed."""

SPRINT_CEREMONY_SCHEDULER_STARTED: str = "workflow.sprint.ceremony_scheduler_started"
"""CeremonyScheduler activated for a sprint."""

SPRINT_CEREMONY_SCHEDULER_STOPPED: str = "workflow.sprint.ceremony_scheduler_stopped"
"""CeremonyScheduler deactivated."""

SPRINT_CEREMONY_DEACTIVATION_HOOK_FAILED: str = (
    "workflow.sprint.ceremony_deactivation_hook_failed"
)
"""on_sprint_deactivated hook raised (teardown continued)."""

SPRINT_CEREMONY_BRIDGE_CREATED: str = "workflow.sprint.ceremony_bridge_created"
"""Sprint ceremony config bridged to meeting type config."""

SPRINT_CEREMONY_POLICY_RESOLVED: str = "workflow.sprint.ceremony_policy_resolved"
"""3-level ceremony policy resolution completed."""

SPRINT_CEREMONY_POLICY_CONFIG_CONFLICT: str = (
    "workflow.sprint.ceremony_policy_config_conflict"
)
"""Ceremony policy has conflicting configuration (e.g. threshold set but
auto-transition disabled)."""

SPRINT_CEREMONY_STRATEGY_CHANGED: str = "workflow.sprint.ceremony_strategy_changed"
"""Ceremony scheduling strategy changed between sprints."""

SPRINT_CEREMONY_NOTIFICATION_FAILED: str = (
    "workflow.sprint.ceremony_notification_failed"
)
"""Strategy migration notification delivery failed (swallowed)."""

SPRINT_CEREMONY_SCHEDULER_START_FAILED: str = (
    "workflow.sprint.ceremony_scheduler_start_failed"
)
"""CeremonyScheduler activation failed (cleanup executed)."""

SPRINT_CEREMONY_STRATEGY_HOOK_FAILED: str = (
    "workflow.sprint.ceremony_strategy_hook_failed"
)
"""Strategy lifecycle hook (on_task_completed) failed -- ceremony
evaluation skipped for this cycle."""

SPRINT_CEREMONY_TRIGGER_FAILED: str = "workflow.sprint.ceremony_trigger_failed"
"""Ceremony trigger_event call failed (swallowed)."""

SPRINT_CEREMONY_BUDGET_SNAPSHOT_FAILED: str = (
    "workflow.sprint.ceremony_budget_snapshot_failed"
)
"""Budget snapshot callable raised inside the scheduler; the
CeremonyEvalContext falls back to (0.0, 0.0) for this evaluation tick
so a transient budget-service failure cannot block ceremony
scheduling."""

SPRINT_CEREMONY_EVAL_CONTEXT_INVALID: str = (
    "workflow.sprint.ceremony_eval_context_invalid"
)
"""CeremonyEvalContext field validation failed."""

VELOCITY_TASK_DRIVEN_NO_TASK_COUNT: str = "workflow.velocity.task_driven_no_task_count"
"""VelocityRecord has no task_completion_count for task-driven calculation."""

VELOCITY_CALENDAR_NO_DURATION: str = "workflow.velocity.calendar_no_duration"
"""CalendarVelocityCalculator received a record with zero duration_days.

Defensive guard -- should not occur with validated input since
``VelocityRecord`` enforces ``duration_days >= 1``.
"""

VELOCITY_MULTI_NO_TASK_COUNT: str = "workflow.velocity.multi_no_task_count"
"""MultiDimensionalVelocityCalculator: no task_completion_count."""

VELOCITY_MULTI_NO_DURATION: str = "workflow.velocity.multi_no_duration"
"""MultiDimensionalVelocityCalculator received a record with zero duration_days.

Defensive guard -- should not occur with validated input since
``VelocityRecord`` enforces ``duration_days >= 1``.
"""

# -- Event-driven strategy events ---------------------------------------------

SPRINT_CEREMONY_EVENT_DEBOUNCE_NOT_MET: str = "workflow.sprint.event_debounce_not_met"
"""Event-driven strategy debounce threshold not yet met."""

SPRINT_CEREMONY_EVENT_COUNTER_INCREMENTED: str = (
    "workflow.sprint.event_counter_incremented"
)
"""Event-driven strategy incremented an internal event counter."""

# -- Budget-driven strategy events --------------------------------------------

SPRINT_CEREMONY_BUDGET_THRESHOLD_CROSSED: str = (
    "workflow.sprint.budget_threshold_crossed"
)
"""Budget-driven strategy detected a budget threshold crossing."""

SPRINT_CEREMONY_BUDGET_THRESHOLD_ALREADY_FIRED: str = (
    "workflow.sprint.budget_threshold_already_fired"
)
"""Budget-driven strategy skipped an already-fired threshold."""

SPRINT_AUTO_TRANSITION_BUDGET: str = "workflow.sprint.auto_transition_budget"
"""Sprint auto-transitioned due to budget exhaustion."""

# -- Budget velocity calculator events ----------------------------------------

VELOCITY_BUDGET_NO_BUDGET_CONSUMED: str = "workflow.velocity.budget_no_budget_consumed"
"""BudgetVelocityCalculator: record has None or zero budget_consumed."""

# -- Throughput-adaptive strategy events --------------------------------------

SPRINT_CEREMONY_THROUGHPUT_BASELINE_SET: str = "workflow.sprint.throughput_baseline_set"
"""Throughput-adaptive strategy established baseline rate."""

SPRINT_CEREMONY_THROUGHPUT_DROP_DETECTED: str = (
    "workflow.sprint.throughput_drop_detected"
)
"""Throughput-adaptive strategy detected a velocity drop."""

SPRINT_CEREMONY_THROUGHPUT_SPIKE_DETECTED: str = (
    "workflow.sprint.throughput_spike_detected"
)
"""Throughput-adaptive strategy detected a velocity spike."""

SPRINT_CEREMONY_THROUGHPUT_COLD_START: str = "workflow.sprint.throughput_cold_start"
"""Throughput-adaptive strategy in cold start (baseline not yet established)."""

# -- External-trigger strategy events -----------------------------------------

SPRINT_CEREMONY_EXTERNAL_EVENT_RECEIVED: str = "workflow.sprint.external_event_received"
"""External-trigger strategy received an external event."""

SPRINT_CEREMONY_EXTERNAL_EVENT_MATCHED: str = "workflow.sprint.external_event_matched"
"""External-trigger strategy matched an external event to a ceremony."""

SPRINT_CEREMONY_EXTERNAL_SOURCE_REGISTERED: str = (
    "workflow.sprint.external_source_registered"
)
"""External-trigger strategy registered event sources."""

SPRINT_CEREMONY_EXTERNAL_SOURCE_CLEARED: str = "workflow.sprint.external_source_cleared"
"""External-trigger strategy cleared event sources."""

# -- Milestone-driven strategy events ----------------------------------------

SPRINT_CEREMONY_MILESTONE_ASSIGNED: str = "workflow.sprint.milestone_assigned"
"""Task assigned to a milestone."""

SPRINT_CEREMONY_MILESTONE_UNASSIGNED: str = "workflow.sprint.milestone_unassigned"
"""Task removed from a milestone."""

SPRINT_CEREMONY_MILESTONE_COMPLETED: str = "workflow.sprint.milestone_completed"
"""All tasks in a milestone are complete."""

SPRINT_CEREMONY_MILESTONE_NOT_READY: str = "workflow.sprint.milestone_not_ready"
"""Milestone has no tasks assigned -- cannot fire."""

SPRINT_AUTO_TRANSITION_MILESTONE: str = "workflow.sprint.auto_transition_milestone"
"""Sprint auto-transitioned at a milestone completion."""
