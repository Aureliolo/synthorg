"""Tests for task controller."""

import uuid
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.state import AppState
from synthorg.core.error_taxonomy import ErrorCode
from tests.unit.api.conftest import FakePersistenceBackend, make_auth_headers, make_task


@pytest.mark.unit
class TestTaskController:
    def test_list_tasks_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_list_tasks_with_data(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["id"] == "task-001"

    def test_list_tasks_filter_by_status(
        self,
        test_client: TestClient[Any],
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
        resp = test_client.get("/api/v1/tasks?status=created")
        body = resp.json()
        assert body["data"][0]["id"] == "t1"

    def test_get_task_found(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.get("/api/v1/tasks/task-001")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "task-001"

    def test_get_task_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/tasks/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_create_task_raises_agent_runtime_not_configured_without_adapter(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """No adapter wired -> ``AgentRuntimeNotConfiguredError`` (409).

        Removes the seam temporarily so the empty-company path is
        exercised by the same controller stack as the success case.
        The shared-app fixture saves + restores the adapter slot for
        us, so the swap is local to this test.
        """
        app_state: AppState = test_client.app.state.app_state
        app_state._task_board_entry_adapter = None
        resp = test_client.post(
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

    def test_create_task(self, test_client: TestClient[Any]) -> None:
        """``POST /tasks`` now hands the filing to the board entry adapter.

        The spine creates the task in its background intake phase; the
        HTTP response is a 202 + submission envelope carrying the
        correlation id the board UI uses to match the eventual
        ``task.created`` WS event.
        """
        resp = test_client.post(
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

    def test_delete_task(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.delete(
            "/api/v1/tasks/task-001",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 204
        assert resp.content == b""

    def test_delete_task_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.delete(
            "/api/v1/tasks/nonexistent",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    def test_oversized_task_id_rejected(self, test_client: TestClient[Any]) -> None:
        long_id = "x" * 129
        resp = test_client.get(
            f"/api/v1/tasks/{long_id}",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestUpdateTask:
    def test_update_task(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.patch(
            "/api/v1/tasks/task-001",
            json={"title": "Updated title"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Updated title"

    def test_update_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.patch(
            "/api/v1/tasks/nonexistent",
            json={"title": "Nope"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    def test_update_requires_write_role(self, test_client: TestClient[Any]) -> None:
        resp = test_client.patch(
            "/api/v1/tasks/task-001",
            json={"title": "Nope"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestTransitionTask:
    def test_transition_task(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.post(
            "/api/v1/tasks/task-001/transition",
            json={
                "target_status": "assigned",
                "assigned_to": "bob",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["status"] == "assigned"

    def test_transition_invalid(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.post(
            "/api/v1/tasks/task-001/transition",
            json={"target_status": "completed"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422

    def test_transition_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            "/api/v1/tasks/nonexistent/transition",
            json={"target_status": "assigned"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    def test_transition_requires_write_role(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            "/api/v1/tasks/task-001/transition",
            json={"target_status": "assigned"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403
