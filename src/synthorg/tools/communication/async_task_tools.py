"""Five steering tools for async task management.

Each tool wraps a single ``AsyncTaskService`` method, exposing
supervisor-facing async task operations as LLM-callable tools.
"""

import json
from typing import Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.communication.async_tasks.models import TaskSpec
from synthorg.communication.async_tasks.service import AsyncTaskService  # noqa: TC001
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.async_task import (
    ASYNC_TASK_TOOL_CANCEL_FAILED,
    ASYNC_TASK_TOOL_CHECK_FAILED,
    ASYNC_TASK_TOOL_START_FAILED,
    ASYNC_TASK_TOOL_UPDATE_FAILED,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.communication._args import (
    CancelAsyncTaskArgs,
    CheckAsyncTaskArgs,
    ListAsyncTasksArgs,
    StartAsyncTaskArgs,
    UpdateAsyncTaskArgs,
)

logger = get_logger(__name__)


class StartAsyncTaskTool(BaseTool):
    """Start a new async task on a subagent."""

    args_model: ClassVar[type[BaseModel] | None] = StartAsyncTaskArgs

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
            parameters_schema=StartAsyncTaskArgs.model_json_schema(),
        )
        self._service = service
        self._supervisor_id = supervisor_id
        self._supervisor_task_id = supervisor_task_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Start an async task and return the task ID.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
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
            reraise_critical(exc)
            logger.warning(
                ASYNC_TASK_TOOL_START_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Failed to start task: {safe_error_description(exc)}",
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"task_id": task_id}),
        )


class CheckAsyncTaskTool(BaseTool):
    """Check the status of an async task."""

    args_model: ClassVar[type[BaseModel] | None] = CheckAsyncTaskArgs

    def __init__(self, *, service: AsyncTaskService) -> None:
        super().__init__(
            name="check_async_task",
            description="Check the status of a background task",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=CheckAsyncTaskArgs.model_json_schema(),
        )
        self._service = service

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Check task status.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            status = await self._service.check_async_task(
                arguments["task_id"],
            )
        except LookupError as exc:
            safe_error = safe_error_description(exc)
            logger.warning(
                ASYNC_TASK_TOOL_CHECK_FAILED,
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=safe_error,
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"status": status.value}),
        )


class UpdateAsyncTaskTool(BaseTool):
    """Send new instructions to a running async task."""

    args_model: ClassVar[type[BaseModel] | None] = UpdateAsyncTaskArgs

    def __init__(self, *, service: AsyncTaskService) -> None:
        super().__init__(
            name="update_async_task",
            description="Send new instructions to a running task",
            category=ToolCategory.COMMUNICATION,
            parameters_schema=UpdateAsyncTaskArgs.model_json_schema(),
        )
        self._service = service

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Update task with new instructions.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            status = await self._service.update_async_task(
                task_id=arguments["task_id"],
                instructions=arguments["instructions"],
            )
        except LookupError as exc:
            safe_error = safe_error_description(exc)
            logger.warning(
                ASYNC_TASK_TOOL_UPDATE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=safe_error,
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"status": status.value}),
        )


class CancelAsyncTaskTool(BaseTool):
    """Cancel a running async task."""

    args_model: ClassVar[type[BaseModel] | None] = CancelAsyncTaskArgs

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
            parameters_schema=CancelAsyncTaskArgs.model_json_schema(),
        )
        self._service = service
        self._supervisor_id = supervisor_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Cancel a task.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            status = await self._service.cancel_async_task(
                task_id=arguments["task_id"],
                supervisor_id=self._supervisor_id,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                ASYNC_TASK_TOOL_CANCEL_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Failed to cancel: {safe_error_description(exc)}",
                is_error=True,
            )
        return ToolExecutionResult(
            content=json.dumps({"status": status.value}),
        )


class ListAsyncTasksTool(BaseTool):
    """List all tracked async tasks for this supervisor."""

    args_model: ClassVar[type[BaseModel] | None] = ListAsyncTasksArgs

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
            parameters_schema=ListAsyncTasksArgs.model_json_schema(),
        )
        self._service = service
        self._supervisor_task_id = supervisor_task_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """List async tasks.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
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
