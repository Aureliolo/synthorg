"""Unit tests for :class:`AnalyticsReadService`.

The service is the analytics controllers' single persistence seam: it
owns the task-list query the overview / trends endpoints build their
aggregates from. These tests pin that ``list_tasks`` delegates to the
repository with an empty filter spec and forwards the result verbatim.
The repository is mocked, so opaque sentinels stand in for ``Task`` rows.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.services.analytics_read_service import AnalyticsReadService
from synthorg.core.task import Task
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository

pytestmark = pytest.mark.unit


async def test_list_tasks_delegates_to_repo_with_empty_filter() -> None:
    repo = AsyncMock(spec=TaskRepository)
    task = MagicMock(spec=Task)
    repo.query.return_value = (task,)
    service = AnalyticsReadService(task_repo=repo)

    result = await service.list_tasks()

    assert result == (task,)
    repo.query.assert_awaited_once()
    spec = repo.query.await_args.args[0]
    assert isinstance(spec, TaskFilterSpec)


async def test_list_tasks_returns_empty_when_repo_empty() -> None:
    repo = AsyncMock(spec=TaskRepository)
    repo.query.return_value = ()
    service = AnalyticsReadService(task_repo=repo)

    assert await service.list_tasks() == ()
