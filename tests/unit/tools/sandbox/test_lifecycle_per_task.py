"""Tests for per-task lifecycle strategy."""

import asyncio

import pytest

from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle
from tests._shared.fake_clock import FakeClock
from tests._shared.lifecycle_pin_check import CountingPin as _CountingPin
from tests._shared.lifecycle_pin_check import settle as _settle

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


class TestPerTaskPinning:
    """A live background job holds off release's immediate teardown."""

    async def test_release_returns_without_destroying_a_pinned_container(
        self,
    ) -> None:
        """release() must not block the task boundary on a running job."""
        clock = FakeClock()
        pin = _CountingPin(live_for=1000)
        strategy = PerTaskStrategy(clock=clock, pin_check=pin, pin_recheck_seconds=0.01)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("pinned")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        # release() itself already returned above; a settle here proves
        # the container is genuinely held, not merely not-yet-checked.
        assert destroyed == []
        await _settle(ticks=10)
        assert destroyed == []
        assert len(pin.calls) >= 3

        await strategy.cleanup_all(destroy_fn=destroy_fn)

    async def test_deferred_teardown_runs_once_job_ends(self) -> None:
        clock = FakeClock()
        pin = _CountingPin(live_for=2)
        strategy = PerTaskStrategy(clock=clock, pin_check=pin, pin_recheck_seconds=0.01)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("pinned-then-free")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        await _settle(ticks=20)

        assert destroyed == ["pinned-then-free"]
        assert len(pin.calls) == 3

    async def test_unpinned_release_destroys_immediately_as_before(self) -> None:
        """``pin_check`` returning False keeps release fully synchronous."""

        async def never_pinned(_container_id: str) -> bool:
            return False

        strategy = PerTaskStrategy(pin_check=never_pinned)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("unpinned")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        # No settle: an unpinned release must have already destroyed by
        # the time it returns, exactly like the no-pin_check default.
        assert destroyed == ["unpinned"]

    async def test_reacquire_while_pinned_wins_over_deferred_teardown(self) -> None:
        """A concurrent acquire during the pinned wait keeps the container."""
        clock = FakeClock()
        pin = _CountingPin(live_for=1000)
        strategy = PerTaskStrategy(clock=clock, pin_check=pin, pin_recheck_seconds=0.01)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("reacquired")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        h1 = await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        await _settle(ticks=5)
        assert destroyed == []

        h2 = await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        assert h1 is h2
        await _settle(ticks=10)
        assert destroyed == []

        await strategy.cleanup_all(destroy_fn=destroy_fn)

    async def test_cleanup_all_cancels_a_pending_pinned_teardown(self) -> None:
        clock = FakeClock()
        pin = _CountingPin(live_for=1000)
        strategy = PerTaskStrategy(clock=clock, pin_check=pin, pin_recheck_seconds=0.01)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("cleanup-pinned")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        await _settle(ticks=5)
        assert destroyed == []

        # Shutdown reclaims the still-pinned container directly, rather
        # than leaving its fate to a background task that outlives the
        # strategy's own cleanup.
        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert destroyed == ["cleanup-pinned"]


class TestGenerationGuardedRelease:
    """A release decided from a snapshot refuses itself once the snapshot is stale.

    A warm reacquire hands back the SAME handle, so the identity check the
    release already carries cannot tell "still the run the sweep read as
    finished" from "reacquired and back in use". The generation can.
    """

    async def test_a_failed_destroy_keeps_the_container_tracked_with_a_generation(
        self,
    ) -> None:
        """A reinstated handle must still be readable by the reclamation sweep.

        ``tracked_owners`` reads a generation for every tracked key, so a
        handle put back without one raises out of every later sweep tick.
        """
        strategy = PerTaskStrategy()

        async def create_fn() -> ContainerHandle:
            return _make_handle("stuck")

        async def failing_destroy(_h: ContainerHandle) -> None:
            msg = "docker daemon gone"
            raise RuntimeError(msg)

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        (before,) = await strategy.tracked_owners()

        await strategy.release(owner_id="t1", destroy_fn=failing_destroy)

        (after,) = await strategy.tracked_owners()
        assert after.key == "t1"
        assert after.generation != before.generation

    async def test_a_failed_deferred_destroy_keeps_a_generation_too(self) -> None:
        clock = FakeClock()
        pin = _CountingPin(live_for=1)
        strategy = PerTaskStrategy(clock=clock, pin_check=pin, pin_recheck_seconds=0.01)

        async def create_fn() -> ContainerHandle:
            return _make_handle("pinned-stuck")

        async def failing_destroy(_h: ContainerHandle) -> None:
            msg = "docker daemon gone"
            raise RuntimeError(msg)

        await strategy.acquire(
            owner_id="t1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        await strategy.release(owner_id="t1", destroy_fn=failing_destroy)
        await _settle(ticks=20)

        (tracked,) = await strategy.tracked_owners()
        assert tracked.key == "t1"

    async def test_a_release_against_the_current_generation_destroys(self) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("gen-1")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        (tracked,) = await strategy.tracked_owners()

        await strategy.release(
            owner_id="t1",
            destroy_fn=destroy_fn,
            expected_generation=tracked.generation,
        )

        assert destroyed == ["gen-1"]
        assert await strategy.tracked_owners() == ()

    async def test_a_reacquire_between_snapshot_and_release_keeps_it(self) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("gen-2")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        first = await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        (snapshot,) = await strategy.tracked_owners()
        # The warm path: the same handle comes back, and only the generation
        # records that anything happened.
        again = await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        assert again is first
        (current,) = await strategy.tracked_owners()
        assert current.generation != snapshot.generation

        await strategy.release(
            owner_id="t1",
            destroy_fn=destroy_fn,
            expected_generation=snapshot.generation,
        )

        assert destroyed == []
        assert (await strategy.tracked_owners())[0].key == "t1"

    async def test_the_owners_own_release_carries_no_generation(self) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("gen-3")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )

        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)

        assert destroyed == ["gen-3"]

    async def test_a_fresh_acquire_after_a_release_never_reuses_a_generation(
        self,
    ) -> None:
        strategy = PerTaskStrategy()
        destroyed: list[str] = []
        created = 0

        async def create_fn() -> ContainerHandle:
            nonlocal created
            created += 1
            return _make_handle(f"gen-4-{created}")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        (stale,) = await strategy.tracked_owners()
        await strategy.release(owner_id="t1", destroy_fn=destroy_fn)
        await strategy.acquire(
            owner_id="t1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )

        await strategy.release(
            owner_id="t1", destroy_fn=destroy_fn, expected_generation=stale.generation
        )

        # Only the first container went; the second is a different run.
        assert destroyed == ["gen-4-1"]
