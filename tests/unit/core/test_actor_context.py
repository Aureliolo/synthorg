"""Tests for the actor-identity context seam."""

import asyncio

import pytest

from synthorg.core.actor_context import (
    ActorIdentity,
    ActorKind,
    actor_scope,
    bind_actor,
    clear_actor,
    current_actor,
    with_actor,
    with_actor_async,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_actor() -> None:
    clear_actor()


def _human(uid: str = "u-1") -> ActorIdentity:
    return ActorIdentity(actor_id=uid, kind=ActorKind.HUMAN, label=uid)


class TestActorIdentity:
    def test_system_constructor(self) -> None:
        actor = ActorIdentity.system("approval-timeout")
        assert actor.kind is ActorKind.SYSTEM
        assert actor.actor_id == "approval-timeout"
        assert actor.label == "approval-timeout"

    def test_is_frozen(self) -> None:
        actor = _human()
        with pytest.raises(Exception, match="frozen"):
            actor.actor_id = "other"  # type: ignore[misc]


class TestBindingAndCurrent:
    def test_none_outside_scope(self) -> None:
        assert current_actor() is None

    def test_bind_then_current(self) -> None:
        bind_actor(_human("alice"))
        got = current_actor()
        assert got is not None
        assert got.actor_id == "alice"

    def test_clear(self) -> None:
        bind_actor(_human())
        clear_actor()
        assert current_actor() is None


class TestActorScope:
    def test_scope_restores_prior(self) -> None:
        bind_actor(_human("outer"))
        with actor_scope(ActorIdentity.system("inner")):
            cur = current_actor()
            assert cur is not None
            assert cur.kind is ActorKind.SYSTEM
        restored = current_actor()
        assert restored is not None
        assert restored.actor_id == "outer"

    def test_nested_scopes(self) -> None:
        with actor_scope(_human("a")):
            with actor_scope(_human("b")):
                cur = current_actor()
                assert cur is not None
                assert cur.actor_id == "b"
            cur = current_actor()
            assert cur is not None
            assert cur.actor_id == "a"
        assert current_actor() is None


class TestAsyncPropagation:
    async def test_propagates_across_task_group(self) -> None:
        seen: list[str | None] = []

        async def observe() -> None:
            cur = current_actor()
            seen.append(cur.actor_id if cur else None)

        with actor_scope(_human("ctx-owner")):
            async with asyncio.TaskGroup() as tg:
                _ = tg.create_task(observe())
                _ = tg.create_task(observe())

        assert seen == ["ctx-owner", "ctx-owner"]

    async def test_child_scope_does_not_leak_to_sibling(self) -> None:
        bind_actor(_human("base"))

        async def mutate() -> str | None:
            with actor_scope(ActorIdentity.system("sys")):
                await asyncio.sleep(0)
                cur = current_actor()
                return cur.actor_id if cur else None

        async def reader() -> str | None:
            await asyncio.sleep(0)
            cur = current_actor()
            return cur.actor_id if cur else None

        # Each task gets its own contextvars copy, so the system scope
        # inside ``mutate`` must not bleed into ``reader``.
        m, r = await asyncio.gather(mutate(), reader())
        assert m == "sys"
        assert r == "base"


class TestDecorators:
    def test_with_actor_sync(self) -> None:
        @with_actor(_human("deco"))
        def who() -> str | None:
            cur = current_actor()
            return cur.actor_id if cur else None

        assert who() == "deco"
        assert current_actor() is None

    def test_with_actor_rejects_async(self) -> None:
        with pytest.raises(TypeError, match="does not support async"):

            @with_actor(_human())
            async def _coro() -> None:  # pragma: no cover - decoration raises
                return None

    async def test_with_actor_async(self) -> None:
        @with_actor_async(ActorIdentity.system("bg"))
        async def who() -> str | None:
            cur = current_actor()
            return cur.actor_id if cur else None

        assert await who() == "bg"
        assert current_actor() is None

    def test_with_actor_async_rejects_sync(self) -> None:
        with pytest.raises(TypeError, match="requires an async"):

            @with_actor_async(_human())  # type: ignore[arg-type]
            def _sync() -> None:  # pragma: no cover - decoration raises
                return None
