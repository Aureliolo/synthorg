"""Tests for per-call lifecycle strategy."""

import pytest

from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

pytestmark = pytest.mark.unit


def _make_handle(cid: str = "c1") -> ContainerHandle:
    return ContainerHandle(container_id=cid)


async def _noop_destroy(_handle: ContainerHandle) -> None:
    """Destroy callback for acquire() (per-call never loses a race)."""


class TestPerCallAcquire:
    """acquire() always creates a new container."""

    async def test_creates_new_container(self) -> None:
        strategy = PerCallStrategy()
        created = _make_handle("new-1")

        async def create_fn() -> ContainerHandle:
            return created

        handle = await strategy.acquire(
            owner_id="agent-1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
        )
        assert handle is created

    async def test_never_reuses(self) -> None:
        strategy = PerCallStrategy()
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        h1 = await strategy.acquire(
            owner_id="a", create_fn=create_fn, destroy_fn=_noop_destroy
        )
        h2 = await strategy.acquire(
            owner_id="a", create_fn=create_fn, destroy_fn=_noop_destroy
        )
        assert h1 is not h2
        assert len(calls) == 2


class TestPerCallRelease:
    """release() is a no-op."""

    async def test_release_noop(self) -> None:
        strategy = PerCallStrategy()
        destroyed: list[ContainerHandle] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.release(
            owner_id="a",
            destroy_fn=destroy_fn,
        )
        assert destroyed == []


class TestPerCallCleanup:
    """cleanup_all() is a no-op."""

    async def test_cleanup_noop(self) -> None:
        strategy = PerCallStrategy()
        destroyed: list[ContainerHandle] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert destroyed == []
