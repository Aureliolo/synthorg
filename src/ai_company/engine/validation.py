"""Pre-flight validation for the agent execution engine.

Pure validation functions extracted from ``AgentEngine`` to keep
the orchestrator focused on execution flow.
"""

from typing import TYPE_CHECKING

from ai_company.core.enums import AgentStatus, TaskStatus
from ai_company.engine.errors import ExecutionStateError
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_ENGINE_INVALID_INPUT,
)

if TYPE_CHECKING:
    from ai_company.core.agent import AgentIdentity
    from ai_company.core.task import Task

logger = get_logger(__name__)

EXECUTABLE_STATUSES = frozenset({TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS})
"""Task statuses the engine will accept for execution.

CREATED tasks lack an assignee; terminal statuses (COMPLETED, CANCELLED),
BLOCKED, IN_REVIEW, FAILED, and INTERRUPTED are not executable.  FAILED
and INTERRUPTED tasks must be reassigned (-> ASSIGNED) before re-execution.
"""


def validate_run_inputs(
    *,
    agent_id: str,
    task_id: str,
    max_turns: int,
    timeout_seconds: float | None,
) -> None:
    """Validate scalar ``run()`` arguments before execution."""
    if max_turns < 1:
        msg = f"max_turns must be >= 1, got {max_turns}"
        logger.warning(
            EXECUTION_ENGINE_INVALID_INPUT,
            agent_id=agent_id,
            task_id=task_id,
            reason=msg,
        )
        raise ValueError(msg)
    if timeout_seconds is not None and timeout_seconds <= 0:
        msg = f"timeout_seconds must be > 0, got {timeout_seconds}"
        logger.warning(
            EXECUTION_ENGINE_INVALID_INPUT,
            agent_id=agent_id,
            task_id=task_id,
            reason=msg,
        )
        raise ValueError(msg)


def validate_agent(identity: AgentIdentity, agent_id: str) -> None:
    """Raise if agent is not ACTIVE."""
    if identity.status != AgentStatus.ACTIVE:
        msg = (
            f"Agent {agent_id} has status {identity.status.value!r}; "
            f"only 'active' agents can run tasks"
        )
        logger.warning(
            EXECUTION_ENGINE_INVALID_INPUT,
            agent_id=agent_id,
            reason=msg,
        )
        raise ExecutionStateError(msg)


def validate_task(
    task: Task,
    agent_id: str,
    task_id: str,
) -> None:
    """Raise if task is not executable or not assigned to this agent."""
    if task.status not in EXECUTABLE_STATUSES:
        msg = (
            f"Task {task_id!r} has status {task.status.value!r}; "
            f"only 'assigned' or 'in_progress' tasks can be executed"
        )
        logger.warning(
            EXECUTION_ENGINE_INVALID_INPUT,
            agent_id=agent_id,
            task_id=task_id,
            reason=msg,
        )
        raise ExecutionStateError(msg)
    if task.assigned_to is not None and task.assigned_to != agent_id:
        msg = (
            f"Task {task_id!r} is assigned to {task.assigned_to!r}, "
            f"not to agent {agent_id!r}"
        )
        logger.warning(
            EXECUTION_ENGINE_INVALID_INPUT,
            agent_id=agent_id,
            task_id=task_id,
            reason=msg,
        )
        raise ExecutionStateError(msg)
