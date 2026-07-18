"""Conformance tests for ``PlanItemCommentRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.plan_comment import PlanItemComment
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_comment_protocol import PlanItemCommentFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _comment(
    label: str,
    *,
    plan_id: str = "plan-1",
    item_id: str = "item-1",
    author: str = "reviewer",
    minute: int = 0,
) -> PlanItemComment:
    return PlanItemComment(
        id=as_uuid(label),
        plan_id=NotBlankStr(plan_id),
        item_id=NotBlankStr(item_id),
        author=NotBlankStr(author),
        body=NotBlankStr(f"Comment {label}"),
        created_at=_T0.replace(minute=minute),
    )


class TestPlanItemCommentRepository:
    async def test_append_and_query_oldest_first(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.plan_comments.append(_comment("c2", minute=5))
        await backend.plan_comments.append(_comment("c1", minute=1))

        result = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=NotBlankStr("plan-1"))
        )
        assert [c.id for c in result] == [as_uuid("c1"), as_uuid("c2")]
        assert result[0].author == "reviewer"

    async def test_query_paginates_with_limit_and_offset(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(5):
            await backend.plan_comments.append(_comment(f"c{i}", minute=i))

        first = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=NotBlankStr("plan-1")), limit=2
        )
        assert [c.id for c in first] == [as_uuid("c0"), as_uuid("c1")]

        second = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=NotBlankStr("plan-1")), limit=2, offset=2
        )
        assert [c.id for c in second] == [as_uuid("c2"), as_uuid("c3")]

    async def test_query_narrows_to_one_item(self, backend: PersistenceBackend) -> None:
        await backend.plan_comments.append(_comment("a", item_id="item-1"))
        await backend.plan_comments.append(_comment("b", item_id="item-2"))

        result = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(
                plan_id=NotBlankStr("plan-1"), item_id=NotBlankStr("item-2")
            )
        )
        assert [c.id for c in result] == [as_uuid("b")]

    async def test_query_scopes_to_the_plan(self, backend: PersistenceBackend) -> None:
        await backend.plan_comments.append(_comment("a", plan_id="plan-1"))
        await backend.plan_comments.append(_comment("b", plan_id="plan-2"))

        result = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=NotBlankStr("plan-2"))
        )
        assert [c.id for c in result] == [as_uuid("b")]

    async def test_duplicate_id_is_rejected(self, backend: PersistenceBackend) -> None:
        await backend.plan_comments.append(_comment("dup"))
        with pytest.raises(DuplicateRecordError):
            await backend.plan_comments.append(_comment("dup"))

    async def test_purge_before_removes_old_comments(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.plan_comments.append(_comment("old", minute=1))
        await backend.plan_comments.append(_comment("new", minute=30))

        removed = await backend.plan_comments.purge_before(_T0.replace(minute=10))
        assert removed == 1
        result = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=NotBlankStr("plan-1"))
        )
        assert [c.id for c in result] == [as_uuid("new")]

    async def test_agent_reply_roundtrips_authorship_and_reply_link(
        self, backend: PersistenceBackend
    ) -> None:
        human = _comment("human", minute=1)
        agent = PlanItemComment(
            id=as_uuid("agent"),
            plan_id=NotBlankStr("plan-1"),
            item_id=NotBlankStr("item-1"),
            author=NotBlankStr("Casey"),
            author_kind="agent",
            author_agent_id=NotBlankStr("agent-cfo"),
            reply_to_id=human.id,
            body=NotBlankStr("Grounded reply."),
            created_at=_T0.replace(minute=2),
        )
        await backend.plan_comments.append(human)
        await backend.plan_comments.append(agent)

        result = await backend.plan_comments.query(
            PlanItemCommentFilterSpec(plan_id=NotBlankStr("plan-1"))
        )
        assert [c.author_kind for c in result] == ["human", "agent"]
        loaded_human, loaded_agent = result
        # A human comment carries no agent id or reply link (the defaults).
        assert loaded_human.author_kind == "human"
        assert loaded_human.author_agent_id is None
        assert loaded_human.reply_to_id is None
        # The agent reply carries its attribution and its parent link intact.
        assert loaded_agent.author_agent_id == "agent-cfo"
        assert loaded_agent.reply_to_id == human.id
