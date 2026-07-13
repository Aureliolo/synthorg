"""Tests for the plan-item comment controller (list / add)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers

_T0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
_PLAN = str(as_uuid("plan-001"))
_ITEM = str(as_uuid("item-1"))


async def _seed_plan(client: LoopAsyncClient) -> None:
    """Persist a plan carrying ``_ITEM`` so comments have a real target."""
    backend = persistence_of(client.app.state.app_state)
    await backend.plans.create(
        Plan(
            id=as_uuid("plan-001"),
            project=NotBlankStr("proj-1"),
            objective_id=NotBlankStr("obj-1"),
            objective_title=NotBlankStr("Ship the thing"),
            parent_task_id=sid("root-task"),
            items=(
                PlanItem(
                    id=_ITEM,
                    title=NotBlankStr("First slice"),
                    description=NotBlankStr("Build the first slice."),
                    acceptance_criteria=(NotBlankStr("It runs"),),
                ),
            ),
            created_at=_T0,
            updated_at=_T0,
        )
    )


async def _seed_comment(client: LoopAsyncClient, label: str, *, minute: int) -> None:
    backend = persistence_of(client.app.state.app_state)
    await backend.plan_comments.append(
        PlanItemComment(
            id=as_uuid(label),
            plan_id=NotBlankStr(_PLAN),
            item_id=NotBlankStr(_ITEM),
            author=NotBlankStr("reviewer"),
            body=NotBlankStr(f"Comment {label}"),
            created_at=_T0.replace(minute=minute),
        )
    )


@pytest.mark.unit
class TestPlanCommentController:
    async def test_list_returns_thread_oldest_first(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_comment(async_test_client, "c2", minute=5)
        await _seed_comment(async_test_client, "c1", minute=1)

        resp = await async_test_client.get(
            f"/api/v1/plans/{_PLAN}/comments",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        bodies = [c["body"] for c in resp.json()["data"]]
        assert bodies == ["Comment c1", "Comment c2"]

    async def test_add_comment_persists_and_attributes_the_author(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_plan(async_test_client)
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
            json={"body": "Consider a smaller first slice."},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["body"] == "Consider a smaller first slice."
        assert data["item_id"] == _ITEM
        # The author comes from the authenticated user, not the request body.
        assert data["author"] == "test-ceo"

        listed = await async_test_client.get(
            f"/api/v1/plans/{_PLAN}/comments",
            headers=make_auth_headers("ceo"),
        )
        assert len(listed.json()["data"]) == 1

    async def test_blank_body_is_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_plan(async_test_client)
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
            json={"body": "   "},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_comment_on_missing_plan_is_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
            json={"body": "No plan exists here."},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_comment_on_item_not_in_plan_is_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_plan(async_test_client)
        other_item = str(as_uuid("item-not-in-plan"))
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{other_item}",
            json={"body": "This item is not on the plan."},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
