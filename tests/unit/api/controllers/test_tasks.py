"""Tests for task controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002

from tests.unit.api.conftest import FakePersistenceBackend, make_task


@pytest.mark.unit
class TestTaskController:
    def test_list_tasks_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

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
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["id"] == "task-001"

    def test_list_tasks_filter_by_status(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        from ai_company.core.enums import TaskStatus

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
        assert body["pagination"]["total"] == 1
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

    def test_create_task(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "New task",
                "description": "Do the thing",
                "type": "development",
                "project": "proj-1",
                "created_by": "alice",
            },
            headers={"X-Human-Role": "ceo"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["title"] == "New task"

    def test_delete_task(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task
        resp = test_client.delete(
            "/api/v1/tasks/task-001",
            headers={"X-Human-Role": "ceo"},
        )
        assert resp.status_code == 200

    def test_delete_task_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.delete(
            "/api/v1/tasks/nonexistent",
            headers={"X-Human-Role": "ceo"},
        )
        assert resp.status_code == 404
