"""Tests for per-task lifecycle strategy."""

import asyncio

import pytest

from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

pytestmark = pytest.mark.unit


def _make_handle(cid: str = "c1") -> ContainerHandle:
    return ContainerHandle(container_id=cid)


async def _noop_destroy(_handle: ContainerHandle) -> None:
    """Destroy callback for acquire() when no race is exercised."""


async def _alive(_handle: ContainerHandle) -> bool:
    """Liveness probe for tests where the container never dies."""
    return True


async def _dead(_handle: ContainerHandle) -> bool:
    """Liveness probe standing in for a container that has exited."""
    return False


class TestPerTaskAcquire:
    """acquire() reuses within same owner, creates for new owners."""

    async def test_creates_new_container(self) -> None:
        strategy = PerTaskStrategy()
        created = _make_handle("task-c1")

        async def create_fn() -> ContainerHandle:
            return created

        handle = await strategy.acquire(
            owner_id="task-1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        assert handle is created

    async def test_reuses_existing_container(self) -> None:
        strategy = PerTaskStrategy()
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        h1 = await strategy.acquire(
            owner_id="task-1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        h2 = await strategy.acquire(
            owner_id="task-1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        assert h1 is h2
        assert len(calls) == 1

    async def test_different_owners_get_different_containers(self) -> None:
        strategy = PerTaskStrategy()
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        h1 = await strategy.acquire(
            owner_id="task-1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        h2 = await strategy.acquire(
            owner_id="task-2",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        assert h1 is not h2
        assert len(calls) == 2

    async def test_concurrent_first_acquire_destroys_loser(self) -> None:
        """A racing first-acquire tears the losing container down."""
        strategy = PerTaskStrategy()
        created: list[ContainerHandle] = []
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            # Yield so both callers pass the initial empty check and
            # each create a distinct handle before either re-locks.
            await asyncio.sleep(0)
            handle = _make_handle(f"race-{len(created)}")
            created.append(handle)
            return handle

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        h1, h2 = await asyncio.gather(
            strategy.acquire(
                owner_id="t1",
                create_fn=create_fn,
                destroy_fn=destroy_fn,
                alive_fn=_alive,
            ),
            strategy.acquire(
                owner_id="t1",
                create_fn=create_fn,
                destroy_fn=destroy_fn,
                alive_fn=_alive,
            ),
        )
        # Both callers see the same retained container; the other one
        # is destroyed rather than leaked.
        assert h1 is h2
        assert len(created) == 2
        assert len(destroyed) == 1
        retained = h1.container_id
        assert destroyed == [
            c.container_id for c in created if c.container_id != retained
        ]


class TestPerTaskLiveness:
    """A warm handle is only reused while its container still runs."""

    async def test_dead_container_is_replaced_and_reaped(self) -> None:
        strategy = PerTaskStrategy()
        created: list[str] = []
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        first = await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        second = await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_dead,
        )

        assert second is not first
        assert second.container_id == "c-1"
        assert destroyed == ["c-0"]

    async def test_one_dead_handle_is_torn_down_once(self) -> None:
        """Two acquires can observe the same dead handle; one owns the reap.

        Reaping on the observation rather than on the eviction destroys the
        same container once per observer, so a container id is handed to
        ``destroy_fn`` twice and the second call acts on something already
        gone.
        """
        strategy = PerTaskStrategy()
        created: list[str] = []
        destroyed: list[str] = []
        both_probing = asyncio.Barrier(2)

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        async def slow_dead_probe(_handle: ContainerHandle) -> bool:
            # A rendezvous, not a flag: the first caller has to still be here
            # when the second arrives, or the first completes its whole
            # acquire (nothing on that path yields) and the second probes the
            # REPLACEMENT, which is a different handle and cannot double-reap
            # whatever the eviction does.
            await both_probing.wait()
            return False

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )

        async with asyncio.TaskGroup() as group:
            probes = [
                group.create_task(
                    strategy.acquire(
                        owner_id="t1",
                        create_fn=create_fn,
                        destroy_fn=destroy_fn,
                        alive_fn=slow_dead_probe,
                    )
                )
                for _ in range(2)
            ]

        assert all(probe.result() is not None for probe in probes)
        assert destroyed.count("c-0") == 1

    async def test_probe_failure_is_treated_as_dead(self) -> None:
        """An unanswerable probe replaces rather than gambles."""
        strategy = PerTaskStrategy()
        created: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def exploding_probe(_handle: ContainerHandle) -> bool:
            msg = "docker daemon unreachable"
            raise RuntimeError(msg)

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        replacement = await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=exploding_probe,
        )

        assert replacement.container_id == "c-1"
        assert len(created) == 2

    async def test_eviction_survives_destroy_failure(self) -> None:
        """A corpse that will not reap still yields a fresh container."""
        strategy = PerTaskStrategy()
        created: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(_handle: ContainerHandle) -> None:
            msg = "no such container"
            raise RuntimeError(msg)

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        replacement = await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_dead,
        )

        assert replacement.container_id == "c-1"


class TestPerTaskRelease:
    """release() destroys the container immediately."""

    async def test_release_destroys(self) -> None:
        strategy = PerTaskStrategy()
        handle = _make_handle("to-destroy")
        destroyed: list[ContainerHandle] = []

        async def create_fn() -> ContainerHandle:
            return handle

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        assert destroyed == [handle]

    async def test_release_unknown_owner_noop(self) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[ContainerHandle] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.release(
            owner_id="nonexistent",
            destroy_fn=destroy_fn,
        )
        assert destroyed == []

    async def test_acquire_after_release_creates_new(self) -> None:
        strategy = PerTaskStrategy()
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        async def destroy_fn(h: ContainerHandle) -> None:
            pass

        h1 = await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        h2 = await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        assert h1 is not h2
        assert len(calls) == 2


class TestPerTaskCleanup:
    """cleanup_all() destroys all tracked containers."""

    async def test_cleanup_destroys_all(self) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[str] = []

        async def make(cid: str) -> ContainerHandle:
            return _make_handle(cid)

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1",
            create_fn=lambda: make("c1"),
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.acquire(
            owner_id="t2",
            create_fn=lambda: make("c2"),
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert sorted(destroyed) == ["c1", "c2"]

    async def test_cleanup_empty_noop(self) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[ContainerHandle] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert destroyed == []

    async def test_cleanup_survives_destroy_failure(self) -> None:
        """cleanup_all continues if one destroy_fn raises."""
        strategy = PerTaskStrategy()
        destroyed: list[str] = []

        async def make(cid: str) -> ContainerHandle:
            return _make_handle(cid)

        async def destroy_fn(h: ContainerHandle) -> None:
            if h.container_id == "c1":
                msg = "docker daemon gone"
                raise RuntimeError(msg)
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1",
            create_fn=lambda: make("c1"),
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.acquire(
            owner_id="t2",
            create_fn=lambda: make("c2"),
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert "c2" in destroyed


class TestPerTaskDoubleRelease:
    """Edge cases around release semantics."""

    async def test_double_release_noop(self) -> None:
        """Second release for same owner is a no-op."""
        strategy = PerTaskStrategy()
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("double-rel")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        assert destroyed == ["double-rel"]
