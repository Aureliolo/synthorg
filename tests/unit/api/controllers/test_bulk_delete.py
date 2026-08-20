"""One operator action, one request, for each of the three entity types.

The dashboard's bulk delete was a loop of single-row DELETEs against endpoints
rate limited per user, so a selection larger than the per-row budget refused its
own tail for a reason that had nothing to do with the rows: an operator clearing
a round's residue got five deletions and a wall of 5001s. These drive the routes
that replaced it.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.controllers._bulk_delete import MAX_BULK_DELETE_IDS
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of
from tests._shared import JsonDict, LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers, make_task

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)


async def _make_project(client: LoopAsyncClient, name: str) -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201
    project_id: str = resp.json()["data"]["id"]
    return project_id


def _plan(plan_id: str) -> Plan:
    return Plan(
        id=as_uuid(plan_id),
        project=NotBlankStr("beachhead"),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-001"),
        objective_title=NotBlankStr("Ship the Tetris game"),
        parent_task_id=NotBlankStr("task-root"),
        status=PlanStatus.PENDING_REVIEW,
        items=(
            PlanItem(
                id=NotBlankStr(sid(f"{plan_id}-item")),
                title=NotBlankStr("Scaffold"),
                description=NotBlankStr("Set up the board"),
                acceptance_criteria=(NotBlankStr("board scaffolded"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
        ),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


async def _post_bulk(
    client: LoopAsyncClient, path: str, ids: list[str]
) -> tuple[int, JsonDict]:
    resp = await client.post(
        f"/api/v1/{path}/bulk-delete",
        json={"ids": ids},
        headers=make_auth_headers("ceo"),
    )
    body: JsonDict = resp.json()
    return resp.status_code, body


class TestBulkDeleteProjects:
    async def test_removes_every_selected_project_in_one_call(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # Six is past the per-row budget of five a minute, which is exactly the
        # selection the old client loop could not settle.
        ids = [
            await _make_project(async_test_client, f"Doomed {index}")
            for index in range(6)
        ]

        status, body = await _post_bulk(async_test_client, "projects", ids)

        assert status == 201
        assert sorted(body["data"]["deleted"]) == sorted(ids)
        assert body["data"]["failed"] == []
        for project_id in ids:
            follow_up = await async_test_client.get(f"/api/v1/projects/{project_id}")
            assert follow_up.status_code == 404

    async def test_reports_the_row_that_refused_without_losing_the_rest(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # A row that is already gone must not decide the fate of the others:
        # reporting only the failure is how an operator concludes that nothing
        # happened to an action they cannot undo.
        real = await _make_project(async_test_client, "Real")

        status, body = await _post_bulk(
            async_test_client, "projects", [real, "proj-does-not-exist"]
        )

        assert status == 201
        assert body["data"]["deleted"] == [real]
        failed = body["data"]["failed"]
        assert len(failed) == 1
        assert failed[0]["id"] == "proj-does-not-exist"
        assert failed[0]["reason"]

    async def test_refuses_a_selection_past_the_cap(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # A bounded request is what keeps one call from becoming an unbounded
        # destructive sweep; a larger selection is two actions the operator can
        # see and stop between.
        ids = [f"proj-{index}" for index in range(MAX_BULK_DELETE_IDS + 1)]

        status, _ = await _post_bulk(async_test_client, "projects", ids)

        assert status == 400

    async def test_refuses_an_empty_selection(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        status, _ = await _post_bulk(async_test_client, "projects", [])

        assert status == 400


class TestBulkDeletePlans:
    async def test_removes_every_selected_plan(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        backend = persistence_of(async_test_client.app.state.app_state)
        ids = []
        for label in ("doomed-a", "doomed-b", "doomed-c"):
            await backend.plans.save(_plan(label))
            ids.append(str(as_uuid(label)))

        status, body = await _post_bulk(async_test_client, "plans", ids)

        assert status == 201
        assert sorted(body["data"]["deleted"]) == sorted(ids)
        for plan_id in ids:
            follow_up = await async_test_client.get(f"/api/v1/plans/{plan_id}")
            assert follow_up.status_code == 404

    async def test_a_plan_that_refuses_leaves_the_others_deleted(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        backend = persistence_of(async_test_client.app.state.app_state)
        await backend.plans.save(_plan("removable"))
        removable = str(as_uuid("removable"))

        status, body = await _post_bulk(
            async_test_client, "plans", [removable, str(as_uuid("never-existed"))]
        )

        assert status == 201
        assert body["data"]["deleted"] == [removable]
        assert len(body["data"]["failed"]) == 1


class TestBulkDeleteTasks:
    async def test_removes_every_selected_task(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        backend = persistence_of(async_test_client.app.state.app_state)
        ids = []
        for label in ("t-a", "t-b", "t-c"):
            await backend.tasks.save(make_task(task_id=label))
            ids.append(str(as_uuid(label)))

        status, body = await _post_bulk(async_test_client, "tasks", ids)

        assert status == 201
        assert sorted(body["data"]["deleted"]) == sorted(ids)
        assert body["data"]["failed"] == []

    async def test_collapses_a_repeated_selection(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # The same row twice is one deletion, not one deletion and one
        # "already gone" the operator has to read as a failure.
        backend = persistence_of(async_test_client.app.state.app_state)
        await backend.tasks.save(make_task(task_id="t-dup"))
        task_id = str(as_uuid("t-dup"))

        status, body = await _post_bulk(async_test_client, "tasks", [task_id, task_id])

        assert status == 201
        assert body["data"]["deleted"] == [task_id]
        assert body["data"]["failed"] == []
