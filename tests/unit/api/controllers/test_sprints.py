"""Tests for the sprint controller (/api/v1/sprints)."""

from collections.abc import Iterator
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_service import SprintService
from tests._shared import LoopAsyncClient, mock_of
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]

_BASE = "/api/v1/sprints"


def _sprint(
    *,
    sprint_id: str = "sprint-1",
    project: str | None = "proj-1",
    status: SprintStatus = SprintStatus.PLANNING,
) -> Sprint:
    return Sprint(
        id=NotBlankStr(sprint_id),
        project=NotBlankStr(project) if project is not None else None,
        name=NotBlankStr("Sprint One"),
        sprint_number=1,
        status=status,
        start_date=(
            "2026-05-22T12:00:00+00:00" if status is not SprintStatus.PLANNING else None
        ),
    )


@pytest.fixture
def wired_sprint_service(
    async_test_client: LoopAsyncClient,
) -> Iterator[_Configured]:
    """Wire a mock SprintService into the shared app; restore afterwards."""
    app_state = async_test_client.app.state.app_state
    original = app_state.slice(EngineStateSlice).sprint_service
    service = mock_of[SprintService]()
    app_state.wire(EngineStateSlice, sprint_service=service)
    try:
        yield service
    finally:
        app_state.wire(EngineStateSlice, sprint_service=original)


@pytest.fixture
def unwired_sprint_service(
    async_test_client: LoopAsyncClient,
) -> Iterator[None]:
    """Force the sprint service unwired for a 503 assertion; restore after."""
    app_state = async_test_client.app.state.app_state
    original = app_state.slice(EngineStateSlice).sprint_service
    app_state.wire(EngineStateSlice, sprint_service=None)
    try:
        yield None
    finally:
        app_state.wire(EngineStateSlice, sprint_service=original)


class TestSprintController:
    async def test_list_returns_sprints(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.list_sprints.return_value = (_sprint(),)
        resp = await async_test_client.get(_BASE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"][0]["id"] == "sprint-1"

    async def test_get_found(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.get_sprint.return_value = _sprint()
        resp = await async_test_client.get(f"{_BASE}/sprint-1")
        assert resp.status_code == 200
        assert resp.json()["data"]["sprint_number"] == 1

    async def test_get_not_found(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.get_sprint.return_value = None
        resp = await async_test_client.get(f"{_BASE}/missing")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_active_none(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.active_sprint.return_value = None
        resp = await async_test_client.get(f"{_BASE}/active?project=proj-1")
        assert resp.status_code == 200
        assert resp.json()["data"] is None

    async def test_create_sprint(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.create_sprint.return_value = _sprint()
        resp = await async_test_client.post(
            _BASE,
            json={"project": "proj-1"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["id"] == "sprint-1"

    async def test_add_task(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.add_task.return_value = _sprint()
        resp = await async_test_client.post(
            f"{_BASE}/sprint-1/tasks",
            json={"task_id": "task-a", "story_points": 3.0},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        wired_sprint_service.add_task.assert_awaited_once()

    async def test_start_sprint(
        self,
        async_test_client: LoopAsyncClient,
        wired_sprint_service: _Configured,
    ) -> None:
        wired_sprint_service.start_sprint.return_value = _sprint(
            status=SprintStatus.ACTIVE
        )
        resp = await async_test_client.post(
            f"{_BASE}/sprint-1/start",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["status"] == "active"

    async def test_list_503_when_unwired(
        self,
        async_test_client: LoopAsyncClient,
        unwired_sprint_service: None,
    ) -> None:
        resp = await async_test_client.get(_BASE)
        assert resp.status_code == 503
