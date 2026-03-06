"""Execution lifecycle event constants."""

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
