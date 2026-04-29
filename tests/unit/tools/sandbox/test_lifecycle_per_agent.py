"""Tests for per-agent lifecycle strategy.

Timer assertions use ``FakeClock`` so the test runs in microseconds
rather than the half-second-to-second wall-clock waits that the
original (real-asyncio.sleep) version paid. ``FakeClock.sleep`` is a
no-op that records its argument and advances virtual time, so the
strategy's grace and idle-timer tasks complete on the next event-loop
tick. ``_settle`` yields control a fixed number of times to let those
tasks acquire the strategy's lock, mutate state, and call ``destroy_fn``.
"""

import asyncio

import pytest

from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


# Number of event-loop yields needed for a timer task to finish: it
# awaits the (no-op) clock sleep, takes the strategy lock, mutates the
# bookkeeping dicts, then awaits the user-supplied destroy_fn. Five
# yields covers the worst-case async-step count without padding.
_SETTLE_TICKS: int = 5


async def _settle(ticks: int = _SETTLE_TICKS) -> None:
    """Yield control ``ticks`` times so scheduled tasks can complete."""
    for _ in range(ticks):
        await asyncio.sleep(0)


def _make_handle(cid: str = "c1") -> ContainerHandle:
    return ContainerHandle(container_id=cid)


def _make_strategy(
    grace: float = 0.1,
    max_idle: float = 300.0,
    clock: FakeClock | None = None,
) -> PerAgentStrategy:
    config = SandboxLifecycleConfig(
        grace_period_seconds=grace,
        max_idle_seconds=max_idle,
    )
    return PerAgentStrategy(config, clock=clock)


class TestPerAgentAcquire:
    """acquire() reuses within same owner, creates for new owners."""

    async def test_creates_new_container(self) -> None:
        strategy = _make_strategy()
        created = _make_handle("agent-c1")

        async def create_fn() -> ContainerHandle:
            return created

        handle = await strategy.acquire(
            owner_id="agent-1",
            create_fn=create_fn,
        )
        assert handle is created

    async def test_reuses_existing_container(self) -> None:
        strategy = _make_strategy()
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        h1 = await strategy.acquire(
            owner_id="agent-1",
            create_fn=create_fn,
        )
        h2 = await strategy.acquire(
            owner_id="agent-1",
            create_fn=create_fn,
        )
        assert h1 is h2
        assert len(calls) == 1

    async def test_different_owners_get_different_containers(self) -> None:
        strategy = _make_strategy()
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        h1 = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
        )
        h2 = await strategy.acquire(
            owner_id="a2",
            create_fn=create_fn,
        )
        assert h1 is not h2
        assert len(calls) == 2


class TestPerAgentRelease:
    """release() starts grace timer; container survives within window."""

    async def test_release_starts_grace_timer(self) -> None:
        clock = FakeClock()
        strategy = _make_strategy(grace=0.1, clock=clock)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("grace-test")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(owner_id="a1", create_fn=create_fn)
        await strategy.release(
            owner_id="a1",
            destroy_fn=destroy_fn,
        )
        # Container should NOT be destroyed immediately.
        assert destroyed == []
        # FakeClock.sleep returns at once so the grace timer task
        # finishes on the next ticks of the event loop.
        await _settle()
        assert destroyed == ["grace-test"]
        # Both timers were armed at release; the grace timer is the
        # one whose duration matches our config.
        assert 0.1 in clock.sleep_calls

    async def test_reacquire_within_grace_cancels_timer(self) -> None:
        clock = FakeClock()
        strategy = _make_strategy(grace=0.5, clock=clock)
        destroyed: list[str] = []
        calls: list[int] = []

        async def create_fn() -> ContainerHandle:
            calls.append(1)
            return _make_handle(f"c-{len(calls)}")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        h1 = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
        )
        await strategy.release(
            owner_id="a1",
            destroy_fn=destroy_fn,
        )
        # Reacquire within grace window: cancels the grace task.
        h2 = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
        )
        assert h1 is h2
        assert len(calls) == 1
        # Settle the loop: the cancelled grace task must NOT run
        # destroy_fn for the reacquired container.
        await _settle()
        assert destroyed == []

    async def test_release_unknown_owner_noop(self) -> None:
        strategy = _make_strategy()
        destroyed: list[ContainerHandle] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.release(
            owner_id="nonexistent",
            destroy_fn=destroy_fn,
        )
        assert destroyed == []


class TestPerAgentCleanup:
    """cleanup_all() cancels timers and destroys all containers."""

    async def test_cleanup_destroys_all(self) -> None:
        strategy = _make_strategy()
        destroyed: list[str] = []

        async def make(cid: str) -> ContainerHandle:
            return _make_handle(cid)

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1",
            create_fn=lambda: make("c1"),
        )
        await strategy.acquire(
            owner_id="a2",
            create_fn=lambda: make("c2"),
        )
        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert sorted(destroyed) == ["c1", "c2"]

    async def test_cleanup_cancels_pending_grace_timers(self) -> None:
        strategy = _make_strategy(grace=10.0)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("timer-test")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
        )
        await strategy.release(
            owner_id="a1",
            destroy_fn=destroy_fn,
        )
        # Timer started but not expired. Cleanup should cancel it.
        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert "timer-test" in destroyed

    async def test_cleanup_empty_noop(self) -> None:
        strategy = _make_strategy()
        destroyed: list[ContainerHandle] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h)

        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert destroyed == []

    async def test_cleanup_survives_destroy_failure(self) -> None:
        """cleanup_all continues if one destroy_fn raises."""
        strategy = _make_strategy()

        async def make(cid: str) -> ContainerHandle:
            return _make_handle(cid)

        destroyed: list[str] = []

        async def destroy_fn(h: ContainerHandle) -> None:
            if h.container_id == "c1":
                msg = "docker daemon gone"
                raise RuntimeError(msg)
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1",
            create_fn=lambda: make("c1"),
        )
        await strategy.acquire(
            owner_id="a2",
            create_fn=lambda: make("c2"),
        )
        await strategy.cleanup_all(destroy_fn=destroy_fn)
        assert "c2" in destroyed


class TestPerAgentIdleTimeout:
    """Idle timeout enforcement via _max_idle."""

    async def test_idle_container_destroyed_after_release(self) -> None:
        """Container destroyed by idle timer after release."""
        clock = FakeClock()
        strategy = _make_strategy(grace=10.0, max_idle=0.15, clock=clock)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("idle-test")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(owner_id="a1", create_fn=create_fn)
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        # Idle timer's polling loop reads the clock under the
        # strategy lock and sleeps the remaining duration. With
        # FakeClock the sleep returns at once and advances virtual
        # time, so the loop converges in two iterations.
        await _settle()
        assert "idle-test" in destroyed

    async def test_zero_max_idle_disables_timer(self) -> None:
        """max_idle=0 means no idle eviction."""
        clock = FakeClock()
        strategy = _make_strategy(grace=10.0, max_idle=0.0, clock=clock)

        async def create_fn() -> ContainerHandle:
            return _make_handle("no-idle")

        async def destroy_fn(h: ContainerHandle) -> None:
            _ = h

        await strategy.acquire(owner_id="a1", create_fn=create_fn)
        # _reset_idle_timer returns early when max_idle <= 0, so no
        # task is scheduled. Settle the loop to be sure nothing runs,
        # then confirm the container is still tracked.
        await _settle()
        h2 = await strategy.acquire(owner_id="a1", create_fn=create_fn)
        assert h2.container_id == "no-idle"
        await strategy.cleanup_all(destroy_fn=destroy_fn)


class TestPerAgentGraceDestroyFailure:
    """Grace-period expiry handles destroy_fn errors gracefully."""

    async def test_grace_expire_survives_destroy_failure(self) -> None:
        clock = FakeClock()
        strategy = _make_strategy(grace=0.05, clock=clock)

        async def create_fn() -> ContainerHandle:
            return _make_handle("fail-destroy")

        async def destroy_fn(h: ContainerHandle) -> None:
            _ = h
            msg = "container already removed"
            raise RuntimeError(msg)

        await strategy.acquire(owner_id="a1", create_fn=create_fn)
        await strategy.release(
            owner_id="a1",
            destroy_fn=destroy_fn,
        )
        # Grace timer fires immediately under FakeClock; destroy_fn
        # raises but the strategy logs and continues.
        await _settle()
        calls: list[int] = []

        async def new_create() -> ContainerHandle:
            calls.append(1)
            return _make_handle("replacement")

        h = await strategy.acquire(owner_id="a1", create_fn=new_create)
        assert h.container_id == "replacement"
        assert len(calls) == 1
        await strategy.cleanup_all(destroy_fn=destroy_fn)
