"""Tests for the plan controller (read / rework / request-changes)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient, as_uuid
from tests.unit.api.conftest import make_auth_headers

_CREATED_AT = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)


def _plan(
    *,
    plan_id: str = "plan-001",
    status: PlanStatus = PlanStatus.PENDING_REVIEW,
    project: str = "beachhead",
    objective_id: str = "obj-001",
) -> Plan:
    return Plan(
        id=as_uuid(plan_id),
        project=NotBlankStr(project),
        objective_id=NotBlankStr(objective_id),
        parent_task_id=NotBlankStr("task-root"),
        items=(
            PlanItem(
                id=NotBlankStr("i1"),
                title=NotBlankStr("Scaffold"),
                description=NotBlankStr("Set up the board"),
            ),
            PlanItem(
                id=NotBlankStr("i2"),
                title=NotBlankStr("Movement"),
                description=NotBlankStr("Drop + rotate"),
                dependencies=(NotBlankStr("i1"),),
            ),
        ),
        status=status,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


async def _seed(client: LoopAsyncClient, plan: Plan) -> None:
    backend = persistence_of(client.app.state.app_state)
    await backend.plans.save(plan)


@pytest.mark.unit
class TestPlanController:
    async def test_list_plans_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/plans")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_get_plan_not_found(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/plans/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_seed_get_and_list(self, async_test_client: LoopAsyncClient) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        get_resp = await async_test_client.get(f"/api/v1/plans/{plan_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()["data"]
        assert data["id"] == plan_id
        assert data["status"] == "pending_review"
        assert len(data["items"]) == 2

        list_resp = await async_test_client.get("/api/v1/plans")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]) == 1

    async def test_list_filter_by_status(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(
            async_test_client,
            _plan(plan_id="approved", status=PlanStatus.APPROVED),
        )
        await _seed(
            async_test_client,
            _plan(plan_id="pending", status=PlanStatus.PENDING_REVIEW),
        )

        resp = await async_test_client.get("/api/v1/plans?status=approved")
        assert resp.status_code == 200
        ids = {row["id"] for row in resp.json()["data"]}
        assert str(as_uuid("approved")) in ids
        assert str(as_uuid("pending")) not in ids

    async def test_list_filter_invalid_status(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/plans?status=bogus")
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "Invalid plan status" in body["error"]

    async def test_edit_reworks_items_and_bumps_version(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": "i1",
                        "title": "Reworked scaffold",
                        "description": "New scope for the board",
                        "owner": "engineering",
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 2
        assert data["status"] == "pending_review"
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Reworked scaffold"
        assert data["items"][0]["owner"] == "engineering"

    async def test_edit_rejects_unresolvable_dependency(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.patch(
            f"/api/v1/plans/{plan_id}",
            json={
                "items": [
                    {
                        "id": "i1",
                        "title": "Broken",
                        "description": "Depends on a ghost",
                        "dependencies": ["does-not-exist"],
                    },
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        assert resp.json()["success"] is False

    async def test_edit_missing_plan_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/plans/ghost",
            json={"items": [{"id": "i1", "title": "X", "description": "Y"}]},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_request_changes_drafts_plan(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed(async_test_client, _plan())
        plan_id = str(as_uuid("plan-001"))

        resp = await async_test_client.post(
            f"/api/v1/plans/{plan_id}/request-changes",
            json={"note": "Split the movement item into drop and rotate"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "draft"

    async def test_request_changes_missing_plan_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/plans/ghost/request-changes",
            json={"note": "please revise"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
