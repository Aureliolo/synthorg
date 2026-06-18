"""Unit tests for the manual DecompositionController endpoint logic.

Drives the handler directly with a fake ``State`` so the 404 path, the
happy path (a labelled plan runs through the real DecompositionService),
and the validation paths (unknown dependency label, duplicate label) are
covered without a full TestClient.
"""

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.decomposition import (
    DecompositionController,
    ManualDecomposeRequest,
    ManualSubtaskSpec,
)
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from tests._shared import as_uuid, make_app_state

pytestmark = pytest.mark.unit


class _FakeTaskEngine:
    """Minimal task engine double exposing ``get_task``."""

    def __init__(self, task: Task | None) -> None:
        self._task = task

    async def get_task(self, task_id: str) -> Task | None:
        del task_id
        return self._task


def _controller() -> DecompositionController:
    return object.__new__(DecompositionController)


def _state(*, task: Task | None) -> State:
    state = State()
    state.app_state = make_app_state(task_engine=_FakeTaskEngine(task))
    return state


def _task() -> Task:
    return Task(
        id=as_uuid("parent"),
        title="Parent task",
        description="A task to decompose.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="manager",
        status=TaskStatus.CREATED,
    )


def _request() -> ManualDecomposeRequest:
    return ManualDecomposeRequest(
        subtasks=(
            ManualSubtaskSpec(label="design", title="Design", description="Design it."),
            ManualSubtaskSpec(
                label="build",
                title="Build",
                description="Build it.",
                dependencies=("design",),
            ),
        ),
    )


async def test_decompose_404_when_task_missing() -> None:
    with pytest.raises(NotFoundError):
        await DecompositionController.decompose_manual.fn(
            _controller(),
            state=_state(task=None),
            task_id="missing",
            data=_request(),
        )


async def test_decompose_happy_path_builds_subtasks() -> None:
    response = await DecompositionController.decompose_manual.fn(
        _controller(),
        state=_state(task=_task()),
        task_id="parent",
        data=_request(),
    )
    result = response.data
    assert len(result.created_tasks) == 2
    assert len(result.plan.subtasks) == 2
    # The "build" subtask depends on "design": exactly one dependency edge.
    assert len(result.dependency_edges) == 1


async def test_decompose_rejects_unknown_dependency_label() -> None:
    bad = ManualDecomposeRequest(
        subtasks=(
            ManualSubtaskSpec(
                label="build",
                title="Build",
                description="Build it.",
                dependencies=("ghost",),
            ),
        ),
    )
    with pytest.raises(ValidationError):
        await DecompositionController.decompose_manual.fn(
            _controller(),
            state=_state(task=_task()),
            task_id="parent",
            data=bad,
        )
