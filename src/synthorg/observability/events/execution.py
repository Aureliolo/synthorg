"""Execution event constants."""

from typing import Final

EXECUTION_TASK_CREATED: Final[str] = "execution.task.created"
EXECUTION_TASK_TRANSITION: Final[str] = "execution.task.transition"
EXECUTION_COST_RECORDED: Final[str] = "execution.cost.recorded"
EXECUTION_CONTEXT_CREATED: Final[str] = "execution.context.created"
EXECUTION_CONTEXT_TURN: Final[str] = "execution.context.turn"
EXECUTION_CONTEXT_SNAPSHOT: Final[str] = "execution.context.snapshot"
EXECUTION_CONTEXT_NO_TASK: Final[str] = "execution.context.no_task"
EXECUTION_MAX_TURNS_EXCEEDED: Final[str] = "execution.max_turns.exceeded"
EXECUTION_TASK_TRANSITION_FAILED: Final[str] = "execution.task.transition_failed"
EXECUTION_CONTEXT_TRANSITION_FAILED: Final[str] = "execution.context.transition_failed"
EXECUTION_COST_ON_TERMINAL: Final[str] = "execution.cost.on_terminal"

EXECUTION_LOOP_START: Final[str] = "execution.loop.start"
EXECUTION_LOOP_TURN_START: Final[str] = "execution.loop.turn_start"
EXECUTION_LOOP_TURN_COMPLETE: Final[str] = "execution.loop.turn_complete"
EXECUTION_LOOP_TOOL_CALLS: Final[str] = "execution.loop.tool_calls"
EXECUTION_LOOP_TERMINATED: Final[str] = "execution.loop.terminated"
EXECUTION_LOOP_BUDGET_EXHAUSTED: Final[str] = "execution.loop.budget_exhausted"
EXECUTION_LOOP_ERROR: Final[str] = "execution.loop.error"

EXECUTION_ENGINE_CREATED: Final[str] = "execution.engine.created"
EXECUTION_ENGINE_START: Final[str] = "execution.engine.start"
EXECUTION_ENGINE_PROMPT_BUILT: Final[str] = "execution.engine.prompt_built"
EXECUTION_ENGINE_COMPLETE: Final[str] = "execution.engine.complete"
EXECUTION_ENGINE_ERROR: Final[str] = "execution.engine.error"
EXECUTION_ENGINE_INVALID_INPUT: Final[str] = "execution.engine.invalid_input"
EXECUTION_ENGINE_TASK_TRANSITION: Final[str] = "execution.engine.task_transition"
EXECUTION_ENGINE_COST_RECORDED: Final[str] = "execution.engine.cost_recorded"
EXECUTION_ENGINE_COST_SKIPPED: Final[str] = "execution.engine.cost_skipped"
EXECUTION_ENGINE_COST_FAILED: Final[str] = "execution.engine.cost_failed"
EXECUTION_ENGINE_TASK_METRICS: Final[str] = "execution.engine.task_metrics"
EXECUTION_ENGINE_TIMEOUT: Final[str] = "execution.engine.timeout"
EXECUTION_ENGINE_BUDGET_STOPPED: Final[str] = "execution.engine.budget_stopped"
EXECUTION_ENGINE_TASK_SYNCED: Final[str] = "execution.engine.task_synced"
EXECUTION_ENGINE_SYNC_FAILED: Final[str] = "execution.engine.sync_failed"

EXECUTION_SHUTDOWN_SIGNAL: Final[str] = "execution.shutdown.signal"
EXECUTION_SHUTDOWN_MANAGER_CREATED: Final[str] = "execution.shutdown.manager_created"
EXECUTION_SHUTDOWN_TASK_TRACKED: Final[str] = "execution.shutdown.task_tracked"
EXECUTION_SHUTDOWN_TASK_ERROR: Final[str] = "execution.shutdown.task_error"
EXECUTION_SHUTDOWN_GRACE_START: Final[str] = "execution.shutdown.grace_start"
EXECUTION_SHUTDOWN_FORCE_CANCEL: Final[str] = "execution.shutdown.force_cancel"
EXECUTION_SHUTDOWN_CLEANUP: Final[str] = "execution.shutdown.cleanup"
EXECUTION_SHUTDOWN_CLEANUP_FAILED: Final[str] = "execution.shutdown.cleanup.failed"
EXECUTION_SHUTDOWN_CLEANUP_TIMEOUT: Final[str] = "execution.shutdown.cleanup.timeout"
EXECUTION_SHUTDOWN_COMPLETE: Final[str] = "execution.shutdown.complete"
EXECUTION_SHUTDOWN_IMMEDIATE_CANCEL: Final[str] = "execution.shutdown.immediate_cancel"
EXECUTION_SHUTDOWN_TOOL_WAIT: Final[str] = "execution.shutdown.tool_wait"
EXECUTION_SHUTDOWN_CHECKPOINT_SAVE: Final[str] = "execution.shutdown.checkpoint_save"
EXECUTION_SHUTDOWN_CHECKPOINT_FAILED: Final[str] = (
    "execution.shutdown.checkpoint_failed"
)
EXECUTION_LOOP_SHUTDOWN: Final[str] = "execution.loop.shutdown"
EXECUTION_LOOP_TASK_CANCELLED: Final[str] = "execution.loop.task_cancelled"

EXECUTION_PLAN_CREATED: Final[str] = "execution.plan.created"
EXECUTION_PLAN_STEP_START: Final[str] = "execution.plan.step_start"
EXECUTION_PLAN_STEP_COMPLETE: Final[str] = "execution.plan.step_complete"
EXECUTION_PLAN_STEP_FAILED: Final[str] = "execution.plan.step_failed"
EXECUTION_PLAN_REPLAN_START: Final[str] = "execution.plan.replan_start"
EXECUTION_PLAN_REPLAN_COMPLETE: Final[str] = "execution.plan.replan_complete"
EXECUTION_PLAN_REPLAN_EXHAUSTED: Final[str] = "execution.plan.replan_exhausted"
EXECUTION_PLAN_PARSE_ERROR: Final[str] = "execution.plan.parse_error"
EXECUTION_PLAN_STEP_TRUNCATED: Final[str] = "execution.plan.step_truncated"
EXECUTION_PLAN_STEP_INDEX_OUT_OF_RANGE: Final[str] = (
    "execution.plan.step_index_out_of_range"
)
EXECUTION_PLAN_STEP_STATUS_UPDATED: Final[str] = "execution.plan.step_status_updated"

EXECUTION_RECOVERY_START: Final[str] = "execution.recovery.start"
EXECUTION_RECOVERY_COMPLETE: Final[str] = "execution.recovery.complete"
EXECUTION_RECOVERY_FAILED: Final[str] = "execution.recovery.failed"
EXECUTION_RECOVERY_SNAPSHOT: Final[str] = "execution.recovery.snapshot"
EXECUTION_RECOVERY_DIAGNOSIS: Final[str] = "execution.recovery.diagnosis"

# Checkpoint callback & resume events
EXECUTION_CHECKPOINT_CALLBACK_FAILED: Final[str] = (
    "execution.checkpoint.callback_failed"
)
EXECUTION_RESUME_START: Final[str] = "execution.resume.start"
EXECUTION_RESUME_COMPLETE: Final[str] = "execution.resume.complete"
EXECUTION_RESUME_FAILED: Final[str] = "execution.resume.failed"

# Loop auto-selection events
EXECUTION_LOOP_AUTO_SELECTED: Final[str] = "execution.loop.auto_selected"
EXECUTION_LOOP_BUDGET_DOWNGRADE: Final[str] = "execution.loop.budget_downgrade"
EXECUTION_LOOP_HYBRID_FALLBACK: Final[str] = "execution.loop.hybrid_fallback"
EXECUTION_LOOP_NO_RULE_MATCH: Final[str] = "execution.loop.no_rule_match"
EXECUTION_LOOP_BUDGET_UNAVAILABLE: Final[str] = "execution.loop.budget_unavailable"

# Hybrid loop events
EXECUTION_HYBRID_STEP_TURN_LIMIT: Final[str] = "execution.hybrid.step_turn_limit"
EXECUTION_HYBRID_PROGRESS_SUMMARY: Final[str] = "execution.hybrid.progress_summary"
EXECUTION_HYBRID_REPLAN_DECIDED: Final[str] = "execution.hybrid.replan_decided"
EXECUTION_HYBRID_TURN_BUDGET_WARNING: Final[str] = (
    "execution.hybrid.turn_budget_warning"
)
EXECUTION_HYBRID_PLAN_TRUNCATED: Final[str] = "execution.hybrid.plan_truncated"
EXECUTION_HYBRID_REPLAN_PARSE_TRACE: Final[str] = "execution.hybrid.replan_parse_trace"
EXECUTION_HYBRID_PROGRESS_SUMMARY_EMPTY: Final[str] = (
    "execution.hybrid.progress_summary_empty"
)
EXECUTION_PLAN_SUMMARY_FALLBACK: Final[str] = "execution.plan.summary_fallback"

# Metrics events
EXECUTION_METRICS_UNEXPECTED_TYPE: Final[str] = "execution.metrics.unexpected_type"

# Project validation events
EXECUTION_PROJECT_VALIDATION_FAILED: Final[str] = "execution.project.validation_failed"

# Credential isolation events
EXECUTION_CREDENTIAL_ISOLATION_VIOLATION: Final[str] = (
    "execution.credential_isolation.violation"
)
