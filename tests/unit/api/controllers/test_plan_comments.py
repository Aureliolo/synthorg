"""Tests for the plan-item comment controller (list / add)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers

_T0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
_PLAN = sid("plan-001")
_ITEM = sid("item-1")


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
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
            json={"body": "   "},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400
