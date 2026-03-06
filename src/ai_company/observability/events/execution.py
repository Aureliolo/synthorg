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
