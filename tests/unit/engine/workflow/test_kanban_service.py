"""Unit tests for the Kanban board service (snapshot + column moves)."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.errors import (
    KanbanInvalidMoveError,
    KanbanWipLimitError,
    TaskNotFoundError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.kanban_columns import KanbanColumn
from synthorg.engine.workflow.kanban_service import KanbanBoardService
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of
from tests.unit.engine.task_engine_helpers import FakePersistence

pytestmark = pytest.mark.unit

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


def _resolver(
    *,
    enforce: bool = False,
    in_progress: int = 5,
    review: int = 3,
    workflow_type: WorkflowType = WorkflowType.AGILE_KANBAN,
) -> _Configured:
    limits = {"kanban_wip_in_progress": in_progress, "kanban_wip_review": review}
    return mock_of[ConfigResolverProtocol](
        get_bool=AsyncMock(return_value=enforce),
        get_int=AsyncMock(side_effect=lambda _ns, key: limits[key]),
        get_enum=AsyncMock(return_value=workflow_type),
    )


def _task(
    label: str,
    status: TaskStatus,
    *,
    project: str = "proj-1",
) -> Task:
    requires_assignee = {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
    }
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description=f"Description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=project,
        created_by="manager",
        assigned_to=str(as_uuid("agent-1")) if status in requires_assignee else None,
        status=status,
    )


async def _seed(persistence: FakePersistence, *tasks: Task) -> None:
    for task in tasks:
        await persistence.tasks.save(task)


def _service(
    persistence: FakePersistence,
    engine: TaskEngine,
    resolver: _Configured,
) -> KanbanBoardService:
    return KanbanBoardService(
        task_repository=persistence.tasks,
        task_engine=engine,
        config_resolver=resolver,
    )


class TestBoardSnapshot:
    async def test_projects_tasks_onto_columns(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(
            persistence,
            _task("t-backlog", TaskStatus.CREATED),
            _task("t-ready", TaskStatus.ASSIGNED),
            _task("t-prog-1", TaskStatus.IN_PROGRESS),
            _task("t-prog-2", TaskStatus.IN_PROGRESS),
            _task("t-review", TaskStatus.IN_REVIEW),
            _task("t-done", TaskStatus.COMPLETED),
        )
        board = await _service(persistence, engine, _resolver()).board_snapshot()
        by_col = {c.column: c for c in board.columns}
        assert by_col[KanbanColumn.BACKLOG].count == 1
        assert by_col[KanbanColumn.READY].count == 1
        assert by_col[KanbanColumn.IN_PROGRESS].count == 2
        assert by_col[KanbanColumn.IN_PROGRESS].limit == 5
        assert by_col[KanbanColumn.IN_PROGRESS].over_limit is False
        assert by_col[KanbanColumn.REVIEW].limit == 3
        assert by_col[KanbanColumn.DONE].limit is None
        assert board.workflow_type is WorkflowType.AGILE_KANBAN
        assert board.enforce_wip is False

    async def test_over_limit_flagged(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(
            persistence,
            *(_task(f"t-{i}", TaskStatus.IN_PROGRESS) for i in range(6)),
        )
        board = await _service(
            persistence, engine, _resolver(in_progress=5)
        ).board_snapshot()
        in_progress = next(
            c for c in board.columns if c.column is KanbanColumn.IN_PROGRESS
        )
        assert in_progress.count == 6
        assert in_progress.over_limit is True

    async def test_project_filter(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(
            persistence,
            _task("t-a", TaskStatus.IN_PROGRESS, project="proj-a"),
            _task("t-b", TaskStatus.IN_PROGRESS, project="proj-b"),
        )
        board = await _service(persistence, engine, _resolver()).board_snapshot(
            project="proj-a"
        )
        in_progress = next(
            c for c in board.columns if c.column is KanbanColumn.IN_PROGRESS
        )
        assert in_progress.count == 1


class TestBoardMove:
    async def test_valid_forward_move(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(persistence, _task("card", TaskStatus.ASSIGNED))
        moved = await _service(persistence, engine, _resolver()).move_task(
            str(as_uuid("card")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
        )
        assert moved.status is TaskStatus.IN_PROGRESS

    async def test_invalid_column_transition_rejected(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        # READY -> DONE is not a legal board transition.
        await _seed(persistence, _task("card", TaskStatus.ASSIGNED))
        with pytest.raises(KanbanInvalidMoveError):
            await _service(persistence, engine, _resolver()).move_task(
                str(as_uuid("card")), KanbanColumn.DONE, requested_by="user-1"
            )

    async def test_off_board_card_rejected(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(persistence, _task("card", TaskStatus.BLOCKED))
        with pytest.raises(KanbanInvalidMoveError):
            await _service(persistence, engine, _resolver()).move_task(
                str(as_uuid("card")), KanbanColumn.READY, requested_by="user-1"
            )

    async def test_missing_card_raises(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await _service(persistence, engine, _resolver()).move_task(
                str(as_uuid("nope")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
            )

    async def test_wip_enforced_blocks_full_column(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(
            persistence,
            *(_task(f"prog-{i}", TaskStatus.IN_PROGRESS) for i in range(5)),
            _task("card", TaskStatus.ASSIGNED),
        )
        service = _service(persistence, engine, _resolver(enforce=True, in_progress=5))
        with pytest.raises(KanbanWipLimitError):
            await service.move_task(
                str(as_uuid("card")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
            )

    async def test_wip_advisory_allows_full_column(
        self, persistence: FakePersistence, engine: TaskEngine
    ) -> None:
        await _seed(
            persistence,
            *(_task(f"prog-{i}", TaskStatus.IN_PROGRESS) for i in range(5)),
            _task("card", TaskStatus.ASSIGNED),
        )
        service = _service(persistence, engine, _resolver(enforce=False, in_progress=5))
        moved = await service.move_task(
            str(as_uuid("card")), KanbanColumn.IN_PROGRESS, requested_by="user-1"
        )
        assert moved.status is TaskStatus.IN_PROGRESS
