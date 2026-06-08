"""Tests for AsyncTaskService."""

from unittest.mock import AsyncMock

import pytest

from synthorg.communication.async_tasks.models import (
    AsyncTaskStatus,
    TaskSpec,
)
from synthorg.communication.async_tasks.service import AsyncTaskService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.enums import MessageType
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.task_engine import TaskEngine
from tests._shared import coerce_id, sid


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-100",
        "title": "Research topic",
        "description": "Find info",
        "type": TaskType.RESEARCH,
        "project": "proj-1",
        "created_by": "supervisor-1",
        "status": TaskStatus.ASSIGNED,
        "assigned_to": "worker-1",
    }
    defaults.update(overrides)
    # Task validates assigned_to consistency with status.
    # For CREATED status, assigned_to must be None.
    status = defaults.get("status")
    if status == TaskStatus.CREATED and "assigned_to" not in overrides:
        defaults["assigned_to"] = None
    defaults["id"] = coerce_id(defaults["id"])
    return Task(**defaults)  # type: ignore[arg-type]


def _make_service() -> tuple[AsyncTaskService, AsyncMock, AsyncMock]:
    engine = AsyncMock(spec=TaskEngine)
    bus = AsyncMock(spec=MessageBus)
    service = AsyncTaskService(task_engine=engine, message_bus=bus)
    return service, engine, bus


@pytest.mark.unit
class TestAsyncTaskServiceStart:
    async def test_start_creates_and_assigns(self) -> None:
        service, engine, _bus = _make_service()
        created_task = _make_task(id="new-1")
        engine.create_task.return_value = created_task
        engine.transition_task.return_value = (
            created_task,
            TaskStatus.CREATED,
        )

        spec = TaskSpec(
            title="Research X",
            description="Find info about X",
            agent_id="researcher-1",
        )
        task_id = await service.start_async_task(
            supervisor_id="supervisor-1",
            task_spec=spec,
        )
        assert task_id == sid("new-1")
        engine.create_task.assert_called_once()
        engine.transition_task.assert_called_once()

    async def test_start_returns_task_id(self) -> None:
        service, engine, _bus = _make_service()
        engine.create_task.return_value = _make_task(id="task-42")
        engine.transition_task.return_value = (
            _make_task(id="task-42"),
            TaskStatus.CREATED,
        )
        spec = TaskSpec(
            title="T",
            description="D",
            agent_id="ag-1",
        )
        result = await service.start_async_task(
            supervisor_id="sup-1",
            task_spec=spec,
        )
        assert result == sid("task-42")


@pytest.mark.unit
class TestAsyncTaskServiceCheck:
    @pytest.mark.parametrize(
        ("engine_status", "expected"),
        [
            (TaskStatus.IN_PROGRESS, AsyncTaskStatus.RUNNING),
            (TaskStatus.COMPLETED, AsyncTaskStatus.COMPLETED),
            (TaskStatus.FAILED, AsyncTaskStatus.FAILED),
            (TaskStatus.CANCELLED, AsyncTaskStatus.CANCELLED),
            (TaskStatus.CREATED, AsyncTaskStatus.PENDING),
        ],
        ids=["running", "completed", "failed", "cancelled", "pending"],
    )
    async def test_status_mapping(
        self,
        engine_status: TaskStatus,
        expected: AsyncTaskStatus,
    ) -> None:
        service, engine, _bus = _make_service()
        task_kwargs: dict[str, object] = {"status": engine_status}
        if engine_status == TaskStatus.CREATED:
            task_kwargs["assigned_to"] = None
        engine.get_task.return_value = _make_task(**task_kwargs)
        status = await service.check_async_task("task-100")
        assert status == expected

    async def test_check_not_found_raises(self) -> None:
        service, engine, _bus = _make_service()
        engine.get_task.return_value = None
        with pytest.raises(LookupError, match="not found"):
            await service.check_async_task("nonexistent")


@pytest.mark.unit
class TestAsyncTaskServiceUpdate:
    async def test_update_sends_message(self) -> None:
        service, engine, bus = _make_service()
        engine.get_task.return_value = _make_task(
            status=TaskStatus.IN_PROGRESS,
            assigned_to="worker-1",
        )
        status = await service.update_async_task(
            task_id="task-100",
            instructions="Focus on section 3",
        )
        assert status == AsyncTaskStatus.RUNNING
        bus.send_direct.assert_called_once()
        call_kwargs = bus.send_direct.call_args
        sent_message = (
            call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["message"]
        )
        assert call_kwargs.kwargs["recipient"] == "worker-1"
        assert sent_message.type == MessageType.CONTEXT_INJECTION
        assert sent_message.parts[0].text == "Focus on section 3"


@pytest.mark.unit
class TestAsyncTaskServiceCancel:
    async def test_cancel_calls_engine(self) -> None:
        service, engine, _bus = _make_service()
        engine.cancel_task.return_value = (
            _make_task(status=TaskStatus.CANCELLED),
            TaskStatus.IN_PROGRESS,
        )
        status = await service.cancel_async_task(
            task_id="task-100",
            supervisor_id="supervisor-1",
        )
        assert status == AsyncTaskStatus.CANCELLED
        engine.cancel_task.assert_called_once_with(
            "task-100",
            requested_by="supervisor-1",
            reason="ASYNC_CANCEL",
        )


@pytest.mark.unit
class TestAsyncTaskServiceList:
    async def test_list_filters_by_parent(self) -> None:
        service, engine, _bus = _make_service()
        engine.list_tasks.return_value = (
            (
                _make_task(
                    id="t1",
                    status=TaskStatus.IN_PROGRESS,
                    parent_task_id="parent-1",
                ),
                _make_task(
                    id="t2",
                    status=TaskStatus.COMPLETED,
                    parent_task_id="parent-1",
                ),
            ),
            2,
        )
        statuses = await service.list_async_tasks(
            supervisor_task_id="parent-1",
        )
        assert len(statuses) == 2

    async def test_list_empty(self) -> None:
        service, engine, _bus = _make_service()
        engine.list_tasks.return_value = ((), 0)
        statuses = await service.list_async_tasks(
            supervisor_task_id="parent-1",
        )
        assert statuses == ()
