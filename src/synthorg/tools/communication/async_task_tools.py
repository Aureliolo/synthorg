"""Five steering tools for async task management.

Each tool wraps a single ``AsyncTaskService`` method, exposing
supervisor-facing async task operations as LLM-callable tools.
"""

import json
from typing import Any, Final

from synthorg.communication.async_tasks.models import TaskSpec
from synthorg.communication.async_tasks.service import AsyncTaskService  # noqa: TC001
from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.async_task import (
    ASYNC_TASK_TOOL_CANCEL_FAILED,
    ASYNC_TASK_TOOL_CHECK_FAILED,
    ASYNC_TASK_TOOL_START_FAILED,
    ASYNC_TASK_TOOL_UPDATE_FAILED,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

_START_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "agent_id": {
            "type": "string",
            "description": "Target agent ID to execute the task",
        },
        "title": {
            "type": "string",
            "description": "Short task title",
        },
        "description": {
            "type": "string",
            "description": "Detailed task description",
        },
    },
    "required": ["agent_id", "title", "description"],
}

_CHECK_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Task ID to check",
        },
    },
    "required": ["task_id"],
}

_UPDATE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Task ID to update",
        },
        "instructions": {
            "type": "string",
            "description": "New instructions for the executing agent",
        },
    },
    "required": ["task_id", "instructions"],
}

_CANCEL_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Task ID to cancel",
        },
    },
    "required": ["task_id"],
}

_LIST_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "supervisor_task_id": {
            "type": "string",
            "description": "Supervisor task ID (optional, uses default if omitted)",
        },
    },
}


class StartAsyncTaskTool(BaseTool):
    """Start a new async task on a subagent."""

    def __init__(
        self,
        *,
        service: AsyncTaskService,
        supervisor_id: str = "supervisor",
        supervisor_task_id: str = "default",
    ) -> None:
        super().__init__(
            name="start_async_task",
            description="Start a background task on a subagent",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=_START_SCHEMA,
        )
        self._service = service
        self._supervisor_id = supervisor_id
        self._supervisor_task_id = supervisor_task_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Start an async task and return the task ID."""
        try:
            spec = TaskSpec(
                title=arguments["title"],
                description=arguments["description"],
                agent_id=arguments["agent_id"],
                parent_task_id=self._supervisor_task_id,
            )
            task_id = await self._service.start_async_task(
                supervisor_id=self._supervisor_id,
                task_spec=spec,
            )
        except Exception as exc:
            logger.warning(
                ASYNC_TASK_TOOL_START_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Failed to start task: {exc}",
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"task_id": task_id}),
        )


class CheckAsyncTaskTool(BaseTool):
    """Check the status of an async task."""

    def __init__(self, *, service: AsyncTaskService) -> None:
        super().__init__(
            name="check_async_task",
            description="Check the status of a background task",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=_CHECK_SCHEMA,
        )
        self._service = service

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Check task status."""
        try:
            status = await self._service.check_async_task(
                arguments["task_id"],
            )
        except LookupError as exc:
            logger.warning(
                ASYNC_TASK_TOOL_CHECK_FAILED,
                error=str(exc),
            )
            return ToolExecutionResult(
                content=str(exc),
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"status": status.value}),
        )


class UpdateAsyncTaskTool(BaseTool):
    """Send new instructions to a running async task."""

    def __init__(self, *, service: AsyncTaskService) -> None:
        super().__init__(
            name="update_async_task",
            description="Send new instructions to a running task",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=_UPDATE_SCHEMA,
        )
        self._service = service

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Update task with new instructions."""
        try:
            status = await self._service.update_async_task(
                task_id=arguments["task_id"],
                instructions=arguments["instructions"],
            )
        except LookupError as exc:
            logger.warning(
                ASYNC_TASK_TOOL_UPDATE_FAILED,
                error=str(exc),
            )
            return ToolExecutionResult(
                content=str(exc),
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"status": status.value}),
        )


class CancelAsyncTaskTool(BaseTool):
    """Cancel a running async task."""

    def __init__(
        self,
        *,
        service: AsyncTaskService,
        supervisor_id: str = "supervisor",
    ) -> None:
        super().__init__(
            name="cancel_async_task",
            description="Cancel a running background task",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=_CANCEL_SCHEMA,
        )
        self._service = service
        self._supervisor_id = supervisor_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Cancel a task."""
        try:
            status = await self._service.cancel_async_task(
                task_id=arguments["task_id"],
                supervisor_id=self._supervisor_id,
            )
        except Exception as exc:
            logger.warning(
                ASYNC_TASK_TOOL_CANCEL_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Failed to cancel: {exc}",
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"status": status.value}),
        )


class ListAsyncTasksTool(BaseTool):
    """List all tracked async tasks for this supervisor."""

    def __init__(
        self,
        *,
        service: AsyncTaskService,
        supervisor_task_id: str = "default",
    ) -> None:
        super().__init__(
            name="list_async_tasks",
            description="List all background tasks",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=_LIST_SCHEMA,
        )
        self._service = service
        self._supervisor_task_id = supervisor_task_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """List async tasks."""
        task_id = arguments.get(
            "supervisor_task_id",
            self._supervisor_task_id,
        )
        children = await self._service.list_async_tasks(task_id)
        return ToolExecutionResult(
            content=json.dumps(
                {
                    "tasks": [
                        {"task_id": tid, "status": s.value} for tid, s in children
                    ],
                },
            ),
        )
