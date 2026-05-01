"""Async task protocol event constants."""

from typing import Final

# Async task lifecycle
ASYNC_TASK_STARTED: Final[str] = "async_task.started"
ASYNC_TASK_START_FAILED: Final[str] = "async_task.start_failed"
ASYNC_TASK_CHECKED: Final[str] = "async_task.checked"
ASYNC_TASK_UPDATED: Final[str] = "async_task.updated"
ASYNC_TASK_UPDATE_FAILED: Final[str] = "async_task.update_failed"
ASYNC_TASK_CANCELLED: Final[str] = "async_task.cancelled"
ASYNC_TASK_CANCEL_FAILED: Final[str] = "async_task.cancel_failed"
ASYNC_TASK_LISTED: Final[str] = "async_task.listed"
ASYNC_TASK_STATE_CHANNEL_UPDATED: Final[str] = "async_task.state_channel.updated"

ASYNC_TASK_STATUS_TRANSITIONED: Final[str] = "async_task.status_transitioned"
"""Async task status transition (any persisted hop).

Emitted on every persisted state change (start -> running -> complete
/ failed / cancelled), not just terminal failures.  Carries
``task_id`` / ``from_status`` / ``to_status`` / ``execution_time_sec``
when the timing is available."""

# Tool-layer failures (separate from service-layer events above)
ASYNC_TASK_TOOL_START_FAILED: Final[str] = "async_task.tool.start_failed"
ASYNC_TASK_TOOL_CHECK_FAILED: Final[str] = "async_task.tool.check_failed"
ASYNC_TASK_TOOL_UPDATE_FAILED: Final[str] = "async_task.tool.update_failed"
ASYNC_TASK_TOOL_CANCEL_FAILED: Final[str] = "async_task.tool.cancel_failed"

# Delegation round limits
DELEGATION_ROUND_SOFT_LIMIT: Final[str] = "delegation.round.soft_limit"
DELEGATION_ROUND_HARD_LIMIT: Final[str] = "delegation.round.hard_limit"

# Background task registry (fire-and-forget tracked tasks per #1404)
BACKGROUND_TASKS_DRAIN_TIMEOUT: Final[str] = "background_tasks.drain.timeout"
