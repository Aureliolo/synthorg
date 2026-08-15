"""Who counts as having worked an initiative.

``Task.assigned_to`` is written when the task enters ASSIGNED, before
anything runs, so the contributor scan has to drop the states that prove no
execution happened. The complement is deliberately generous: a run that
failed or was cancelled partway is work somebody did, and status alone
cannot tell that apart from one abandoned in the queue.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.contributors import initiative_contributors
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import mock_of, sid

pytestmark = pytest.mark.unit

_PROJECT = sid("proj-contrib")


def _task(label: str, *, status: TaskStatus, assignee: str | None) -> Task:
    """Build a project task in *status* held by *assignee*.

    Returns:
        The task.
    """
    return Task(
        title=NotBlankStr(label),
        description=NotBlankStr(f"description for {label}"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr(_PROJECT),
        created_by=NotBlankStr("operator"),
        status=status,
        assigned_to=None if assignee is None else NotBlankStr(assignee),
    )


def _repo(*tasks: Task) -> Any:  # type: ignore[explicit-any]  # mock_of returns Any by design
    """Return a task store answering one page of *tasks*.

    Returns:
        The repository double.
    """
    return mock_of[TaskRepository](query=AsyncMock(return_value=tuple(tasks)))


async def test_a_queued_assignee_is_not_a_contributor() -> None:
    """ASSIGNED means the work was handed over, not that it ran."""
    repo = _repo(
        _task("queued", status=TaskStatus.ASSIGNED, assignee=sid("agent-queued")),
        _task("ran", status=TaskStatus.IN_PROGRESS, assignee=sid("agent-ran")),
    )

    contributors = await initiative_contributors(repo, project_id=_PROJECT)

    assert contributors == (sid("agent-ran"),)


@pytest.mark.parametrize(
    "status",
    [
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
        TaskStatus.SUSPENDED,
        TaskStatus.BLOCKED,
    ],
)
async def test_work_that_left_the_queue_counts(status: TaskStatus) -> None:
    """Everything past ASSIGNED may have run, so its assignee counts.

    FAILED and CANCELLED are the load-bearing rows: the retrospective that
    reads this wants the runs that went wrong, and dropping them to avoid
    counting an abandoned queue entry would lose exactly those.
    """
    repo = _repo(_task("work", status=status, assignee=sid("agent-worked")))

    contributors = await initiative_contributors(repo, project_id=_PROJECT)

    assert contributors == (sid("agent-worked"),)


async def test_the_lead_contributes_without_taking_a_task() -> None:
    """Leading the initiative is contributing to it."""
    repo = _repo(
        _task("queued", status=TaskStatus.ASSIGNED, assignee=sid("agent-queued")),
    )

    contributors = await initiative_contributors(
        repo,
        project_id=_PROJECT,
        lead_id=NotBlankStr(sid("agent-lead")),
    )

    assert contributors == (sid("agent-lead"),)


async def test_an_all_queued_initiative_has_no_contributors() -> None:
    """Nobody has started, so the answer is nobody rather than everybody."""
    repo = _repo(
        _task("a", status=TaskStatus.ASSIGNED, assignee=sid("agent-a")),
        _task("b", status=TaskStatus.ASSIGNED, assignee=sid("agent-b")),
        _task("c", status=TaskStatus.CREATED, assignee=None),
    )

    contributors = await initiative_contributors(repo, project_id=_PROJECT)

    assert contributors == ()
