"""Tests for task controller."""

import uuid

import pytest

from synthorg.api.state import AppState
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.engine.state import EngineStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import FakePersistenceBackend, make_auth_headers, make_task


@pytest.mark.unit
class TestTaskController:
    async def test_list_tasks_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_list_tasks_with_data(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = await async_test_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["id"] == "task-001"

    async def test_list_tasks_filter_by_status(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        from synthorg.core.enums import TaskStatus

        t1 = make_task(task_id="t1")
        t2 = make_task(
            task_id="t2",
            status=TaskStatus.ASSIGNED,
            assigned_to="bob",
        )
        fake_persistence.tasks._tasks["t1"] = t1
        fake_persistence.tasks._tasks["t2"] = t2
        resp = await async_test_client.get("/api/v1/tasks?status=created")
        body = resp.json()
        assert body["data"][0]["id"] == "t1"

    async def test_get_task_found(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = await async_test_client.get("/api/v1/tasks/task-001")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "task-001"

    async def test_get_task_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/tasks/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_create_task_raises_agent_runtime_not_configured_without_adapter(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """No adapter wired -> ``AgentRuntimeNotConfiguredError`` (409).

        Removes the seam temporarily so the empty-company path is
        exercised by the same controller stack as the success case.
        The shared-app fixture saves + restores the adapter slot for
        us, so the swap is local to this test.
        """
        app_state: AppState = async_test_client.app.state.app_state
        app_state.wire(EngineStateSlice, task_board_entry_adapter=None)
        resp = await async_test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Filed against an empty company",
                "description": "Nothing should run.",
                "type": "development",
                "project": "proj-1",
                "created_by": "alice",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["error_detail"]
        assert detail["error_code"] == ErrorCode.AGENT_RUNTIME_NOT_CONFIGURED.value

    async def test_create_task(self, async_test_client: LoopAsyncClient) -> None:
        """``POST /tasks`` now hands the filing to the board entry adapter.

        The spine creates the task in its background intake phase; the
        HTTP response is a 202 + submission envelope carrying the
        correlation id the board UI uses to match the eventual
        ``task.created`` WS event.
        """
        resp = await async_test_client.post(
            "/api/v1/tasks",
            json={
                "title": "New task",
                "description": "Do the thing",
                "type": "development",
                "project": "proj-1",
                "created_by": "alice",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["title"] == "New task"
        assert data["project"] == "proj-1"
        assert data["status"] == "submitted"
        # correlation_id is the UUID4 stamped onto the WorkItem by
        # ``TaskBoardFiling``'s default factory; validate the format so
        # a regression that swaps it for a random string surfaces here.
        assert isinstance(data["correlation_id"], str)
        parsed = uuid.UUID(data["correlation_id"])
        assert parsed.version == 4

    async def test_delete_task(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = await async_test_client.delete(
            "/api/v1/tasks/task-001",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 204
        assert resp.content == b""

    async def test_delete_task_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/tasks/nonexistent",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_oversized_task_id_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_id = "x" * 129
        resp = await async_test_client.get(
            f"/api/v1/tasks/{long_id}",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestUpdateTask:
    async def test_update_task(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = await async_test_client.patch(
            "/api/v1/tasks/task-001",
            json={"title": "Updated title"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Updated title"

    async def test_update_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.patch(
            "/api/v1/tasks/nonexistent",
            json={"title": "Nope"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_update_requires_write_role(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/tasks/task-001",
            json={"title": "Nope"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestTransitionTask:
    async def test_transition_task(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = await async_test_client.post(
            "/api/v1/tasks/task-001/transition",
            json={
                "target_status": "assigned",
                "assigned_to": "bob",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["status"] == "assigned"

    async def test_transition_invalid(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = await async_test_client.post(
            "/api/v1/tasks/task-001/transition",
            json={"target_status": "completed"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422

    async def test_transition_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/tasks/nonexistent/transition",
            json={"target_status": "assigned"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_transition_requires_write_role(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/tasks/task-001/transition",
            json={"target_status": "assigned"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403
