"""Unit tests for the advisory sprint gate on Kanban board moves."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import SprintTaskNotInBacklogError
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.kanban_columns import KanbanColumn
from synthorg.engine.workflow.kanban_service import KanbanBoardService
from synthorg.engine.workflow.sprint_service import SprintService
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of
from tests.unit.engine.task_engine_helpers import FakePersistence

pytestmark = pytest.mark.unit

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


def _resolver() -> _Configured:
    limits = {"kanban_wip_in_progress": 5, "kanban_wip_review": 3}
    return mock_of[ConfigResolverProtocol](
        get_bool=AsyncMock(return_value=False),
        get_int=AsyncMock(side_effect=lambda _ns, key: limits[key]),
        get_enum=AsyncMock(return_value=WorkflowType.AGILE_KANBAN),
    )


def _card() -> Task:
    return Task(
        id=as_uuid("card"),
        title="Card",
        description="A ready card",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-1"),
        created_by="manager",
        assigned_to=str(as_uuid("agent-1")),
        status=TaskStatus.ASSIGNED,
    )


def _service(
    persistence: FakePersistence,
    engine: TaskEngine,
    sprint_service: _Configured,
) -> KanbanBoardService:
    return KanbanBoardService(
        task_repository=persistence.tasks,
        task_engine=engine,
        config_resolver=_resolver(),
        sprint_service=sprint_service,
    )


async def test_gate_blocks_task_outside_backlog(
    persistence: FakePersistence, engine: TaskEngine
) -> None:
    await persistence.tasks.save(_card())
    sprint_service = mock_of[SprintService](
        is_task_workable=AsyncMock(return_value=False)
    )
    service = _service(persistence, engine, sprint_service)
    with pytest.raises(SprintTaskNotInBacklogError):
        await service.move_task(
            str(as_uuid("card")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
        )


async def test_gate_allows_backlog_task(
    persistence: FakePersistence, engine: TaskEngine
) -> None:
    await persistence.tasks.save(_card())
    sprint_service = mock_of[SprintService](
        is_task_workable=AsyncMock(return_value=True)
    )
    service = _service(persistence, engine, sprint_service)
    moved = await service.move_task(
        str(as_uuid("card")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
    )
    assert moved.status is TaskStatus.IN_PROGRESS


async def test_no_gate_without_sprint_service(
    persistence: FakePersistence, engine: TaskEngine
) -> None:
    await persistence.tasks.save(_card())
    service = _service(persistence, engine, None)
    moved = await service.move_task(
        str(as_uuid("card")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
    )
    assert moved.status is TaskStatus.IN_PROGRESS
