"""Tests for the plan-item comment controller (list / add)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.engine.plan_review.reply import AgentReply
from synthorg.engine.state import EngineStateSlice
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers


class _FixedReplyService:
    """A reply service that answers every comment with a preset agent reply."""

    def __init__(self, reply: AgentReply) -> None:
        self._reply = reply

    async def reply(
        self,
        *,
        plan: Plan,
        item: PlanItem,
        comment_body: str,
        active: tuple[AgentIdentity, ...],
    ) -> AgentReply:
        del plan, item, comment_body, active
        return self._reply


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
                    expected_artifacts=(NotBlankStr("src/slice.py"),),
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

    async def test_wired_reply_service_appends_an_attributed_agent_reply(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_plan(async_test_client)
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(EngineStateSlice)
        app_state.wire(
            EngineStateSlice,
            plan_item_reply_service=_FixedReplyService(
                AgentReply(
                    author=NotBlankStr("Casey"),
                    author_agent_id=NotBlankStr("agent-cfo"),
                    body=NotBlankStr("The new ledger nets out FX exposure."),
                )
            ),
        )
        try:
            resp = await async_test_client.post(
                f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
                json={"body": "Why this ledger?"},
                headers=make_auth_headers("ceo"),
            )
            assert resp.status_code == 201
            # The POST returns the operator's comment as soon as it persists; the
            # agent reply is generated in a fire-and-forget background task. Drain
            # it so the assertion sees the settled thread rather than racing it.
            await app_state.drain_entry_background_tasks()
            listed = await async_test_client.get(
                f"/api/v1/plans/{_PLAN}/comments",
                headers=make_auth_headers("ceo"),
            )
            thread = listed.json()["data"]
            assert len(thread) == 2
            human, agent = thread
            assert human["author_kind"] == "human"
            assert agent["author_kind"] == "agent"
            assert agent["author"] == "Casey"
            assert agent["author_agent_id"] == "agent-cfo"
            # The agent reply links back to the operator's comment.
            assert agent["reply_to_id"] == human["id"]
        finally:
            app_state.swap_slice(original)

    async def test_reply_to_a_sibling_comment_is_accepted(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_plan(async_test_client)
        await _seed_comment(async_test_client, "parent", minute=1)
        parent_id = str(as_uuid("parent"))
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
            json={"body": "A threaded reply.", "reply_to_id": parent_id},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["reply_to_id"] == parent_id

    async def test_reply_to_nonexistent_comment_is_404(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _seed_plan(async_test_client)
        ghost = str(as_uuid("no-such-comment"))
        resp = await async_test_client.post(
            f"/api/v1/plans/{_PLAN}/comments/items/{_ITEM}",
            json={"body": "Reply to nothing.", "reply_to_id": ghost},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

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
