"""Tests for task controller."""

import uuid
from datetime import UTC, datetime

import pytest

from synthorg.api.state import AppState
from synthorg.core.deleted_entity import (
    DeletedEntity,
    DeletedEntityKind,
    tombstone_id,
)
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from tests._shared import LoopAsyncClient, sid
from tests.unit.api.conftest import FakePersistenceBackend, make_auth_headers, make_task

_PLAN_AT = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)


def _plan_for(task_id: str) -> Plan:
    """A minimal durable plan whose objective is *task_id*."""
    return Plan(
        project=NotBlankStr("beachhead"),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(task_id),
        items=(
            PlanItem(
                id=NotBlankStr(sid("item-1")),
                title=NotBlankStr("Scaffold"),
                description=NotBlankStr("Set up the board"),
                acceptance_criteria=(NotBlankStr("board scaffolded"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
        ),
        created_at=_PLAN_AT,
        updated_at=_PLAN_AT,
    )


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
        fake_persistence.tasks._tasks[str(task.id)] = task
        resp = await async_test_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["id"] == sid("task-001")

    async def test_list_tasks_filter_by_status(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        from synthorg.core.task_enums import TaskStatus

        t1 = make_task(task_id="t1")
        t2 = make_task(
            task_id="t2",
            status=TaskStatus.ASSIGNED,
            assigned_to="bob",
        )
        fake_persistence.tasks._tasks[str(t1.id)] = t1
        fake_persistence.tasks._tasks[str(t2.id)] = t2
        resp = await async_test_client.get("/api/v1/tasks?status=created")
        body = resp.json()
        assert body["data"][0]["id"] == sid("t1")

    async def test_get_task_found(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        task = make_task()
        fake_persistence.tasks._tasks[str(task.id)] = task
        resp = await async_test_client.get(f"/api/v1/tasks/{sid('task-001')}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == sid("task-001")

    async def test_get_task_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/tasks/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_a_deleted_task_says_what_it_was(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """This is the read the tombstones exist for.

        Dropping the foreign keys let a task be deleted while its cost,
        metric and decision rows kept naming it. Resolving one of those
        names comes here, so "not found" alone would be the dangling
        reference the pins used to prevent.
        """
        await fake_persistence.deleted_entities.append(
            DeletedEntity(
                id=tombstone_id(DeletedEntityKind.TASK, sid("task-gone")),
                entity_kind=DeletedEntityKind.TASK,
                entity_id=NotBlankStr(sid("task-gone")),
                display_name=NotBlankStr("Implement the game engine"),
                deleted_by=NotBlankStr("Aurelio"),
                deleted_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            )
        )

        # A plan deleted under the same identifier. Tombstones are keyed by
        # (kind, entity_id), so answering a task lookup with a plan's name
        # would be worse than answering nothing.
        await fake_persistence.deleted_entities.append(
            DeletedEntity(
                id=tombstone_id(DeletedEntityKind.PLAN, sid("task-gone")),
                entity_kind=DeletedEntityKind.PLAN,
                entity_id=NotBlankStr(sid("task-gone")),
                display_name=NotBlankStr("Ship the browser game"),
                deleted_by=NotBlankStr("Someone else"),
                deleted_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            )
        )

        resp = await async_test_client.get(f"/api/v1/tasks/{sid('task-gone')}")

        assert resp.status_code == 404
        detail = resp.text
        assert "Implement the game engine" in detail
        assert "Aurelio" in detail
        assert "Ship the browser game" not in detail

    async def test_a_deleted_task_keeps_its_own_error_code(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """A client tells a missing task from a missing plan by code.

        The tombstone read is the one path where that matters most, since
        it is what a surviving cost or decision row resolves through.
        """
        await fake_persistence.deleted_entities.append(
            DeletedEntity(
                id=tombstone_id(DeletedEntityKind.TASK, sid("task-coded")),
                entity_kind=DeletedEntityKind.TASK,
                entity_id=NotBlankStr(sid("task-coded")),
                display_name=NotBlankStr("Wire the scoreboard"),
                deleted_by=NotBlankStr("Aurelio"),
                deleted_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            )
        )

        resp = await async_test_client.get(f"/api/v1/tasks/{sid('task-coded')}")

        assert resp.status_code == 404
        assert resp.json()["error_detail"]["error_code"] == ErrorCode.TASK_NOT_FOUND

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
        fake_persistence.tasks._tasks[str(task.id)] = task
        resp = await async_test_client.delete(
            f"/api/v1/tasks/{sid('task-001')}",
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

    async def test_delete_refused_while_a_plan_owns_the_task(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Deleting the objective used to silently orphan its plan.

        The orphan kept running, reached the review queue asking for a
        decision on work with no parent, and could then not be removed at
        all.
        """
        task = make_task()
        fake_persistence.tasks._tasks[str(task.id)] = task
        await fake_persistence.plans.save(_plan_for(str(task.id)))

        resp = await async_test_client.delete(
            f"/api/v1/tasks/{sid('task-001')}",
            headers=make_auth_headers("ceo"),
        )

        assert resp.status_code == 409
        detail = resp.json()["error_detail"]
        assert detail["error_code"] == ErrorCode.PLAN_PARENT_TASK_IN_USE.value
        # The message names the plan and the way out, not a constraint.
        assert "/plans/" in resp.text
        # Refused, not partially applied.
        assert str(task.id) in fake_persistence.tasks._tasks

    async def test_delete_succeeds_once_the_plan_is_resolved(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """The refusal is a gate, not a trap."""
        task = make_task()
        fake_persistence.tasks._tasks[str(task.id)] = task
        plan = _plan_for(str(task.id))
        await fake_persistence.plans.save(plan)
        await fake_persistence.plans.delete(NotBlankStr(str(plan.id)))

        resp = await async_test_client.delete(
            f"/api/v1/tasks/{sid('task-001')}",
            headers=make_auth_headers("ceo"),
        )

        assert resp.status_code == 204

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
        fake_persistence.tasks._tasks[str(task.id)] = task
        resp = await async_test_client.patch(
            f"/api/v1/tasks/{sid('task-001')}",
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
            f"/api/v1/tasks/{sid('task-001')}",
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
        fake_persistence.tasks._tasks[str(task.id)] = task
        resp = await async_test_client.post(
            f"/api/v1/tasks/{sid('task-001')}/transition",
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
        fake_persistence.tasks._tasks[str(task.id)] = task
        resp = await async_test_client.post(
            f"/api/v1/tasks/{sid('task-001')}/transition",
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
            f"/api/v1/tasks/{sid('task-001')}/transition",
            json={"target_status": "assigned"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403
