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
from collections.abc import Awaitable, Callable

import pytest

from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
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


def _make_strategy(
    grace: float = 0.1,
    max_idle: float = 300.0,
    clock: FakeClock | None = None,
    pin_check: Callable[[str], Awaitable[bool]] | None = None,
    pin_recheck_seconds: float = 0.01,
) -> PerAgentStrategy:
    config = SandboxLifecycleConfig(
        grace_period_seconds=grace,
        max_idle_seconds=max_idle,
    )
    return PerAgentStrategy(
        config,
        clock=clock,
        pin_check=pin_check,
        pin_recheck_seconds=pin_recheck_seconds,
    )


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
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
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
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        h2 = await strategy.acquire(
            owner_id="agent-1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
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
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        h2 = await strategy.acquire(
            owner_id="a2",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        assert h1 is not h2
        assert len(calls) == 2

    async def test_concurrent_first_acquire_destroys_loser(self) -> None:
        """A racing first-acquire tears the losing container down.

        Regression: the loser path used to read ``_destroy_fns`` which
        was only populated by ``release()``, so a parallel first
        acquire leaked the extra warm container.
        """
        strategy = _make_strategy()
        created: list[ContainerHandle] = []
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            await asyncio.sleep(0)
            handle = _make_handle(f"race-{len(created)}")
            created.append(handle)
            return handle

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        h1, h2 = await asyncio.gather(
            strategy.acquire(
                owner_id="a1",
                create_fn=create_fn,
                destroy_fn=destroy_fn,
                alive_fn=_alive,
            ),
            strategy.acquire(
                owner_id="a1",
                create_fn=create_fn,
                destroy_fn=destroy_fn,
                alive_fn=_alive,
            ),
        )
        assert h1 is h2
        assert len(created) == 2
        assert len(destroyed) == 1
        retained = h1.container_id
        assert destroyed == [
            c.container_id for c in created if c.container_id != retained
        ]


class TestPerAgentLiveness:
    """A warm handle is only reused while its container still runs.

    Regression: a live run had two agent containers exit 137 mid-task.
    The strategy kept handing the dead handles back, so every remaining
    tool call for those agents failed against a container that no longer
    existed, for the life of the process. Reuse is now conditional on a
    probe.
    """

    async def test_dead_container_is_replaced(self) -> None:
        strategy = _make_strategy()
        created: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        first = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        second = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_dead,
        )

        assert second is not first
        assert created == ["c-0", "c-1"]
        assert second.container_id == "c-1"

    async def test_dead_container_is_handed_to_destroy(self) -> None:
        """The corpse is reaped, not orphaned, when it is evicted."""
        strategy = _make_strategy()
        created: list[str] = []
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_dead,
        )

        assert destroyed == ["c-0"]

    async def test_two_stale_probes_destroy_the_container_once(self) -> None:
        """Reaping is the eviction's other half, so only the evictor reaps.

        The liveness probe runs outside the lock, which is deliberate: it is
        a round-trip to the container backend and holding the lock across it
        would serialise every acquire in the process. The consequence is that
        two acquires for one owner can hold the same handle and both find it
        dead. Only one of them takes it out of the cache; reaping regardless
        of that hands one container to ``destroy_fn`` twice.
        """
        strategy = _make_strategy()
        created: list[str] = []
        destroyed: list[str] = []
        both_probing = asyncio.Barrier(2)

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(handle: ContainerHandle) -> None:
            destroyed.append(handle.container_id)

        async def dead_once_both_hold_it(_handle: ContainerHandle) -> bool:
            # A rendezvous rather than a sleep: it puts both probes on the
            # same handle before either can evict, which is the whole race.
            await both_probing.wait()
            return False

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )

        async with asyncio.TaskGroup() as group:
            probes = [
                group.create_task(
                    strategy.acquire(
                        owner_id="a1",
                        create_fn=create_fn,
                        destroy_fn=destroy_fn,
                        alive_fn=dead_once_both_hold_it,
                    )
                )
                for _ in range(2)
            ]

        # Both acquires returned a live container; neither was left holding
        # the handle they agreed was dead.
        assert all(probe.result().container_id != "c-0" for probe in probes)

        # The loser of the ensuing create race is destroyed too, which is a
        # different container and a different rule; this one is about c-0.
        assert destroyed.count("c-0") == 1

    async def test_probe_failure_is_treated_as_dead(self) -> None:
        """An unanswerable probe replaces rather than gambles.

        A probe that raises leaves the container's state unknown, and
        the cost of guessing "alive" wrongly is every remaining call in
        the task; the cost of guessing "dead" wrongly is one container.
        """
        strategy = _make_strategy()
        created: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def exploding_probe(_handle: ContainerHandle) -> bool:
            msg = "docker daemon unreachable"
            raise RuntimeError(msg)

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=_alive,
        )
        replacement = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=_noop_destroy,
            alive_fn=exploding_probe,
        )

        assert replacement.container_id == "c-1"
        assert len(created) == 2

    async def test_eviction_survives_destroy_failure(self) -> None:
        """A corpse that will not reap still yields a fresh container.

        Failing the acquire here would deny the caller the very
        container the eviction exists to give them.
        """
        strategy = _make_strategy()
        created: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(_handle: ContainerHandle) -> None:
            msg = "no such container"
            raise RuntimeError(msg)

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        replacement = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_dead,
        )

        assert replacement.container_id == "c-1"

    async def test_eviction_clears_the_idle_timer(self) -> None:
        """The dead owner's timers go with it.

        A surviving idle timer would later pop whatever handle occupies
        the slot, destroying the replacement container out from under a
        running task.
        """
        clock = FakeClock()
        strategy = _make_strategy(grace=10.0, max_idle=0.15, clock=clock)
        created: list[str] = []
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            created.append(f"c-{len(created)}")
            return _make_handle(created[-1])

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        # Re-acquire before the timers fire: the cached handle is dead,
        # so it is evicted and replaced.
        replacement = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_dead,
        )
        await _settle()

        assert replacement.container_id == "c-1"
        assert destroyed == ["c-0"]

        await strategy.cleanup_all(destroy_fn=destroy_fn)


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

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
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
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.release(
            owner_id="a1",
            destroy_fn=destroy_fn,
        )
        # Reacquire within grace window: cancels the grace task.
        h2 = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
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
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.acquire(
            owner_id="a2",
            create_fn=lambda: make("c2"),
            destroy_fn=destroy_fn,
            alive_fn=_alive,
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
            destroy_fn=destroy_fn,
            alive_fn=_alive,
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
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.acquire(
            owner_id="a2",
            create_fn=lambda: make("c2"),
            destroy_fn=destroy_fn,
            alive_fn=_alive,
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

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        # Idle timer's polling loop reads the clock under the
        # strategy lock and sleeps the remaining duration. With
        # FakeClock the sleep returns at once and advances virtual
        # time, so the loop converges in two iterations.
        await _settle()
        # Both grace (10.0s) and idle (0.15s) timers run under the
        # same FakeClock. Asserting destruction happened isn't enough
        # because grace-path cleanup alone could destroy the container
        # without ever exercising the idle path; assert the idle
        # duration was actually scheduled so the test fails if the
        # idle timer never armed.
        assert any(call == pytest.approx(0.15) for call in clock.sleep_calls)
        assert "idle-test" in destroyed

    async def test_zero_max_idle_disables_timer(self) -> None:
        """max_idle=0 means no idle eviction."""
        clock = FakeClock()
        strategy = _make_strategy(grace=10.0, max_idle=0.0, clock=clock)

        async def create_fn() -> ContainerHandle:
            return _make_handle("no-idle")

        async def destroy_fn(h: ContainerHandle) -> None:
            _ = h

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        # _reset_idle_timer returns early when max_idle <= 0, so no
        # task is scheduled. Settle the loop to be sure nothing runs,
        # then confirm the FakeClock was never armed: any call to
        # ``clock.sleep`` would mean the idle timer fired despite the
        # opt-out, so the assertion catches a regression that the
        # acquire-time tracking-only check would miss.
        await _settle()
        assert clock.sleep_calls == ()
        h2 = await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        assert h2.container_id == "no-idle"
        assert clock.sleep_calls == ()
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

        await strategy.acquire(
            owner_id="a1",
            create_fn=create_fn,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
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

        h = await strategy.acquire(
            owner_id="a1",
            create_fn=new_create,
            destroy_fn=destroy_fn,
            alive_fn=_alive,
        )
        assert h.container_id == "replacement"
        assert len(calls) == 1
        await strategy.cleanup_all(destroy_fn=destroy_fn)


class TestPerAgentPinning:
    """A live background job holds off grace and idle teardown alike."""

    async def test_pinned_container_survives_grace_expiry(self) -> None:
        clock = FakeClock()
        pin = _CountingPin(live_for=1000)
        strategy = _make_strategy(grace=0.1, max_idle=300.0, clock=clock, pin_check=pin)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("pinned")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        # Grace duration elapses, but the job is still "running" for the
        # first three pin checks: the container must not be torn down.
        await _settle(ticks=10)
        assert destroyed == []
        assert len(pin.calls) >= 3

        await strategy.cleanup_all(destroy_fn=destroy_fn)

    async def test_container_destroyed_once_job_ends(self) -> None:
        clock = FakeClock()
        pin = _CountingPin(live_for=2)
        strategy = _make_strategy(grace=0.1, max_idle=300.0, clock=clock, pin_check=pin)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("pinned-then-free")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        await _settle(ticks=20)

        assert destroyed == ["pinned-then-free"]
        # Three calls: two reporting the job still live, one reporting
        # it ended -- proof the loop actually rechecked rather than
        # destroying on the first pass.
        assert len(pin.calls) == 3

    async def test_pinned_container_survives_idle_expiry(self) -> None:
        clock = FakeClock()
        pin = _CountingPin(live_for=1000)
        strategy = _make_strategy(grace=10.0, max_idle=0.05, clock=clock, pin_check=pin)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("idle-pinned")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        # `release()` starts a grace timer alongside the idle timer under
        # test, and FakeClock.sleep completes after a single yield
        # regardless of the requested duration -- so grace's own 10.0s
        # wait does not actually outlast idle's 0.05s here, and either
        # timer finding the container unpinned would destroy it. Cancel
        # grace explicitly so only the idle timer is left running, and
        # assert `pin.calls` grew to prove THAT timer's own recheck loop
        # executed -- `destroyed == []` alone holds for either path and
        # would not catch a regression confined to idle's own pinning.
        async with strategy._lock:
            strategy._cancel_timer("a1")
        calls_before_idle_recheck = len(pin.calls)
        await _settle(ticks=10)
        assert len(pin.calls) > calls_before_idle_recheck
        assert destroyed == []

        await strategy.cleanup_all(destroy_fn=destroy_fn)

    async def test_unpinned_container_behaves_exactly_as_before(self) -> None:
        """``pin_check`` returning False immediately changes nothing."""
        clock = FakeClock()

        async def never_pinned(_container_id: str) -> bool:
            return False

        strategy = _make_strategy(grace=0.1, clock=clock, pin_check=never_pinned)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("unpinned")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        await strategy.acquire(
            owner_id="a1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        await _settle()

        assert destroyed == ["unpinned"]

    async def test_reacquire_while_pinned_cancels_the_wait(self) -> None:
        """A concurrent acquire still wins even mid pin-recheck loop.

        ``_cancel_timer``/``_cancel_idle_timer`` cancel the whole
        grace/idle task regardless of which await point it is stalled
        at, so this must work identically to the existing
        reacquire-within-grace case even while the task is looping on
        the pin check rather than sleeping the plain grace duration.
        """
        clock = FakeClock()
        pin = _CountingPin(live_for=1000)
        strategy = _make_strategy(grace=0.05, clock=clock, pin_check=pin)
        destroyed: list[str] = []

        async def create_fn() -> ContainerHandle:
            return _make_handle("reacquired")

        async def destroy_fn(h: ContainerHandle) -> None:
            destroyed.append(h.container_id)

        h1 = await strategy.acquire(
            owner_id="a1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        await strategy.release(owner_id="a1", destroy_fn=destroy_fn)
        # Let the grace timer fire and enter its pin-recheck loop.
        await _settle(ticks=5)
        assert destroyed == []

        h2 = await strategy.acquire(
            owner_id="a1", create_fn=create_fn, destroy_fn=destroy_fn, alive_fn=_alive
        )
        assert h1 is h2
        await _settle(ticks=5)
        assert destroyed == []

        await strategy.cleanup_all(destroy_fn=destroy_fn)
