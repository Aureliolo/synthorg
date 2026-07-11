"""Unit tests for :class:`AnalyticsReadService`.

The service is the analytics controllers' single persistence seam: it
owns the task-list query the overview / trends endpoints build their
aggregates from. These tests pin that ``list_tasks`` delegates to the
repository with an empty filter spec and forwards the result verbatim,
and that ``list_in_progress`` scopes the query to in-flight tasks. The
repository is mocked, so opaque sentinels stand in for ``Task`` rows.
"""

import pytest

from synthorg.api.services.analytics_read_service import AnalyticsReadService
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from tests._shared import mock_of

pytestmark = pytest.mark.unit


async def test_list_tasks_delegates_to_repo_with_empty_filter() -> None:
    repo = mock_of[TaskRepository]()
    task = mock_of[Task]()
    repo.query.return_value = (task,)
    service = AnalyticsReadService(task_repo=repo)

    result = await service.list_tasks()

    assert result == (task,)
    repo.query.assert_awaited_once()
    spec = repo.query.await_args.args[0]
    assert isinstance(spec, TaskFilterSpec)
    # Verify the spec is genuinely the empty/default filter, not just the
    # right type -- the analytics aggregates depend on an unfiltered query.
    assert spec == TaskFilterSpec()


async def test_list_tasks_returns_empty_when_repo_empty() -> None:
    repo = mock_of[TaskRepository]()
    repo.query.return_value = ()
    service = AnalyticsReadService(task_repo=repo)

    assert await service.list_tasks() == ()


async def test_list_in_progress_filters_to_in_progress_status() -> None:
    repo = mock_of[TaskRepository]()
    task = mock_of[Task]()
    repo.query.return_value = (task,)
    service = AnalyticsReadService(task_repo=repo)

    result = await service.list_in_progress()

    assert result == (task,)
    repo.query.assert_awaited_once()
    spec = repo.query.await_args.args[0]
    assert isinstance(spec, TaskFilterSpec)
    # Utilisation must count only in-flight work, so the query is scoped to
    # IN_PROGRESS rather than scanning the whole task set per department.
    assert spec == TaskFilterSpec(status=TaskStatus.IN_PROGRESS)
