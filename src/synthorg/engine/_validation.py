"""Input validation helpers for AgentEngine.

Pure validation functions extracted from :mod:`agent_engine` to keep
the main orchestrator under the 800-line limit.
"""

import re
from typing import TYPE_CHECKING, Final

from synthorg.core.enums import AgentStatus, TaskStatus
from synthorg.engine.errors import ExecutionStateError
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_CREDENTIAL_ISOLATION_VIOLATION,
    EXECUTION_ENGINE_INVALID_INPUT,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

logger = get_logger(__name__)

_CREDENTIAL_KEY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)token"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)bearer"),
)

_EXECUTABLE_STATUSES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS},
)
"""Task statuses the engine will accept for execution.

CREATED tasks lack an assignee; terminal statuses (COMPLETED, CANCELLED),
BLOCKED, IN_REVIEW, FAILED, and INTERRUPTED are not executable.  FAILED
and INTERRUPTED tasks must be reassigned (-> ASSIGNED) before re-execution.
"""


def _collect_credential_keys(
    data: object,
    prefix: str,
    violations: list[str],
) -> None:
    """Recursively scan dict keys for credential patterns."""
    if isinstance(data, dict):
        for key in data:
            if not isinstance(key, str):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if any(p.search(key) for p in _CREDENTIAL_KEY_PATTERNS):
                violations.append(path)
            _collect_credential_keys(data[key], path, violations)
    elif isinstance(data, list | tuple):
        for i, item in enumerate(data):
            _collect_credential_keys(
                item,
                f"{prefix}[{i}]",
                violations,
            )


def validate_task_metadata(
    task: Task,
    agent_id: str,
    task_id: str,
) -> None:
    """Reject task metadata keys that match credential patterns.

    Recursively scans all dict keys in ``task.metadata`` (including
    nested dicts and dicts inside lists) so credentials cannot hide
    inside nested structures.

    Args:
        task: The task to validate.
        agent_id: Agent identifier for logging.
        task_id: Task identifier for logging.

    Raises:
        ExecutionStateError: If any metadata key matches a credential
            pattern.
    """
    if not task.metadata:
        return
    violations: list[str] = []
    _collect_credential_keys(task.metadata, "", violations)
    violations.sort()
    if violations:
        msg = (
            f"Task {task_id!r} metadata contains credential-like keys: "
            f"{violations}; credentials must flow through the sandbox "
            f"credential proxy, not task metadata"
        )
        logger.error(
            EXECUTION_CREDENTIAL_ISOLATION_VIOLATION,
            agent_id=agent_id,
            task_id=task_id,
            violating_keys=violations,
        )
        raise ExecutionStateError(msg)


def validate_run_inputs(
    *,
    agent_id: str,
    task_id: str,
    max_turns: int,
    timeout_seconds: float | None,
) -> None:
    """Validate scalar ``run()`` arguments before execution.

    Raises:
        ValueError: When ``max_turns < 1`` or ``timeout_seconds`` is
            set to a non-positive value.
    """
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
    """Raise if agent is not ACTIVE.

    Raises:
        ExecutionStateError: When the agent's status is not
            :attr:`AgentStatus.ACTIVE`.
    """
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
    """Raise if task is not executable or not assigned to this agent.

    Raises:
        ExecutionStateError: When the task is not in an executable
            status (``assigned`` / ``in_progress``) or is not assigned
            to ``agent_id``.
    """
    if task.status not in _EXECUTABLE_STATUSES:
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
