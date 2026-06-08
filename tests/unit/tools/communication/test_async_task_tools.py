"""Tests for async task steering tools."""

import json
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.async_tasks.models import AsyncTaskStatus
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.communication.async_task_tools import (
    CancelAsyncTaskTool,
    CheckAsyncTaskTool,
    ListAsyncTasksTool,
    StartAsyncTaskTool,
    UpdateAsyncTaskTool,
)


def _make_service() -> AsyncMock:
    return AsyncMock()


@pytest.mark.unit
class TestStartAsyncTaskTool:
    def test_metadata(self) -> None:
        tool = StartAsyncTaskTool(service=_make_service())
        assert tool.name == "start_async_task"
        assert tool.category == ToolCategory.COMMUNICATION

    async def test_success(self) -> None:
        svc = _make_service()
        svc.start_async_task.return_value = "task-42"
        tool = StartAsyncTaskTool(service=svc)
        result = await tool.execute(
            arguments={
                "agent_id": "researcher-1",
                "title": "Research X",
                "description": "Find info",
            },
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["task_id"] == "task-42"

    async def test_failure(self) -> None:
        svc = _make_service()
        svc.start_async_task.side_effect = RuntimeError("engine down")
        tool = StartAsyncTaskTool(service=svc)
        result = await tool.execute(
            arguments={
                "agent_id": "ag-1",
                "title": "T",
                "description": "D",
            },
        )
        assert result.is_error
        assert "engine down" in result.content


@pytest.mark.unit
class TestCheckAsyncTaskTool:
    def test_metadata(self) -> None:
        tool = CheckAsyncTaskTool(service=_make_service())
        assert tool.name == "check_async_task"

    async def test_success(self) -> None:
        svc = _make_service()
        svc.check_async_task.return_value = AsyncTaskStatus.RUNNING
        tool = CheckAsyncTaskTool(service=svc)
        result = await tool.execute(arguments={"task_id": "t-1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "running"

    async def test_not_found(self) -> None:
        svc = _make_service()
        svc.check_async_task.side_effect = LookupError("not found")
        tool = CheckAsyncTaskTool(service=svc)
        result = await tool.execute(arguments={"task_id": "bad"})
        assert result.is_error


@pytest.mark.unit
class TestUpdateAsyncTaskTool:
    def test_metadata(self) -> None:
        tool = UpdateAsyncTaskTool(service=_make_service())
        assert tool.name == "update_async_task"

    async def test_success(self) -> None:
        svc = _make_service()
        svc.update_async_task.return_value = AsyncTaskStatus.RUNNING
        tool = UpdateAsyncTaskTool(service=svc)
        result = await tool.execute(
            arguments={
                "task_id": "t-1",
                "instructions": "Focus section 3",
            },
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "running"


@pytest.mark.unit
class TestCancelAsyncTaskTool:
    def test_metadata(self) -> None:
        tool = CancelAsyncTaskTool(service=_make_service())
        assert tool.name == "cancel_async_task"

    async def test_success(self) -> None:
        svc = _make_service()
        svc.cancel_async_task.return_value = AsyncTaskStatus.CANCELLED
        tool = CancelAsyncTaskTool(service=svc)
        result = await tool.execute(arguments={"task_id": "t-1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "cancelled"


@pytest.mark.unit
class TestListAsyncTasksTool:
    def test_metadata(self) -> None:
        tool = ListAsyncTasksTool(service=_make_service())
        assert tool.name == "list_async_tasks"

    async def test_empty(self) -> None:
        svc = _make_service()
        svc.list_async_tasks.return_value = ()
        tool = ListAsyncTasksTool(service=svc)
        result = await tool.execute(arguments={})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["tasks"] == []

    async def test_with_tasks(self) -> None:
        svc = _make_service()
        svc.list_async_tasks.return_value = (
            ("task-1", AsyncTaskStatus.RUNNING),
            ("task-2", AsyncTaskStatus.COMPLETED),
        )
        tool = ListAsyncTasksTool(service=svc)
        result = await tool.execute(arguments={})
        data = json.loads(result.content)
        assert data["tasks"] == [
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-2", "status": "completed"},
        ]
