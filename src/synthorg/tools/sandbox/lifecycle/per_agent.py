"""Per-agent sandbox lifecycle strategy.

Reuses a container for all tool calls by the same agent.  After
``release()`` the container is kept alive for a configurable grace
period, then destroyed.  A subsequent ``acquire()`` within the grace
window cancels the timer and returns the warm container.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_ACQUIRE,
    SANDBOX_LIFECYCLE_CLEANUP,
    SANDBOX_LIFECYCLE_DESTROY_FAILED,
    SANDBOX_LIFECYCLE_GRACE_EXPIRED,
    SANDBOX_LIFECYCLE_IDLE_EXPIRED,
    SANDBOX_LIFECYCLE_RELEASE,
    SANDBOX_LIFECYCLE_TEARDOWN_PINNED,
)
from synthorg.tools.sandbox.lifecycle._liveness import log_stale, probe_alive, reap
from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)

#: Default wait between pin rechecks while a grace/idle teardown is held
#: off by a live background job. Independent of ``grace_period_seconds``:
#: a short grace period should not turn into a tight poll loop, and a long
#: one should not silently wait minutes to notice a job just finished.
DEFAULT_PIN_RECHECK_SECONDS: Final[float] = 15.0


class PerAgentStrategy:
    """Reuse a container per *owner_id*, destroy after grace period.

    A container whose ``pin_check`` reports a live background job is not
    torn down by either timer: grace/idle expiry reschedule themselves
    instead of destroying, at ``pin_recheck_seconds`` intervals, until
    the job ends (or its own ``max_duration_seconds`` ceiling force-ends
    it, which is ``pin_check``'s own responsibility, not this
    strategy's). Nothing else about the class changes: with no
    ``pin_check`` wired (the default), every existing caller -- tests
    included -- observes exactly today's grace/idle behaviour.
    """

    def __init__(
        self,
        config: SandboxLifecycleConfig,
        *,
        clock: Clock | None = None,
        pin_check: Callable[[str], Awaitable[bool]] | None = None,
        pin_recheck_seconds: float = DEFAULT_PIN_RECHECK_SECONDS,
    ) -> None:
        """Initialize the per-agent lifecycle strategy.

        Args:
            config: Lifecycle configuration (grace period, max idle, etc.).
            clock: Time source for grace + idle timers. Defaults to
                ``SystemClock``; tests pass ``FakeClock`` for
                deterministic timer expiry without real waiting.
            pin_check: Async predicate, keyed by ``container_id``,
                answering whether a live background job is still
                running inside it. ``None`` (the default) means no
                background-job feature is wired, so grace/idle expiry
                behave exactly as they always have.
            pin_recheck_seconds: How often a pinned container's
                grace/idle teardown rechecks ``pin_check`` while held
                off. Unused when ``pin_check`` is ``None``.
        """
        self._grace_seconds = config.grace_period_seconds
        self._max_idle = config.max_idle_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._pin_check = pin_check
        self._pin_recheck_seconds = pin_recheck_seconds
        self._containers: dict[str, ContainerHandle] = {}
        self._last_used: dict[str, float] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._idle_timers: dict[str, asyncio.Task[None]] = {}
        self._destroy_fns: dict[str, Callable[[ContainerHandle], Awaitable[None]]] = {}
        self._lock = asyncio.Lock()

    def bind_pin_check(self, pin_check: Callable[[str], Awaitable[bool]]) -> None:
        """Wire *pin_check* in after construction.

        Exists because ``pin_check`` is naturally a bound method of the
        Docker sandbox itself (it needs that sandbox's own background-job
        registry and kill primitive), while this strategy must already
        exist for ``build_sandbox_backends`` to construct that sandbox --
        a genuine construction-order cycle, broken here rather than by
        reaching into ``self._pin_check`` from outside the class. Callers
        must bind before the first ``acquire()`` of a container that
        should be pinnable; grace/idle expiry only ever reads
        ``self._pin_check`` for a container it is already timing, so a
        bind before any container exists is race-free by construction.
        """
        self._pin_check = pin_check

    async def _await_unpinned(self, owner_id: str) -> ContainerHandle | None:
        """Block until *owner_id*'s cached container is gone or unpinned.

        Consulted by both grace and idle expiry immediately before they
        would otherwise destroy a container, so neither can tear one
        down out from under a live background job. Probed outside the
        lock, like ``alive_fn`` in :meth:`_reusable_handle`: it may be a
        DB round-trip, and holding the lock across it would serialise
        every acquire in the process behind one pin check.

        Returns:
            The still-cached handle once nothing pins it, or ``None``
            once the container is already gone (the other timer, or a
            liveness eviction, got there first).
        """
        # Upper-bounded by pin_check's own self-cleaning expiry (a job
        # past its own max_duration_seconds is force-cancelled, so this
        # never outlives a wedged job indefinitely); cleanup_all()
        # cancels this task on shutdown like every other per-owner pump.
        # lint-allow: long-running-loop-kill-switch -- bounded by
        # pin_check expiry; cleanup cancels.
        while True:
            async with self._lock:
                handle = self._containers.get(owner_id)
            if handle is None:
                return None
            if self._pin_check is None or not await self._pin_check(
                handle.container_id
            ):
                return handle
            logger.debug(
                SANDBOX_LIFECYCLE_TEARDOWN_PINNED,
                strategy="per-agent",
                owner_id=owner_id,
                container_id=handle.container_id,
            )
            await self._clock.sleep(self._pin_recheck_seconds)

    @property
    def reuses_container(self) -> bool:
        """``True`` -- one warm container per agent (grace teardown)."""
        return True

    async def _evict(
        self,
        owner_id: str,
        handle: ContainerHandle,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Drop a dead handle from the cache and tear its remains down."""
        async with self._lock:
            # Identity-checked: a concurrent acquire may already have
            # replaced the entry, and evicting that one would destroy a
            # container somebody else is about to use.
            evicted = self._containers.get(owner_id) is handle
            if evicted:
                self._containers.pop(owner_id, None)
                self._last_used.pop(owner_id, None)
                self._destroy_fns.pop(owner_id, None)
                self._cancel_timer(owner_id)
                self._cancel_idle_timer(owner_id)
        if not evicted:
            # Two probes can find the same handle dead at once. Only one of
            # them takes it out of the cache, and reaping is the eviction's
            # other half: doing it here regardless would destroy one
            # container twice, so the loser returns and leaves the reap to
            # whoever actually removed it.
            return
        await reap(
            handle,
            strategy="per-agent",
            owner_id=owner_id,
            destroy_fn=destroy_fn,
        )

    async def _reusable_handle(
        self,
        owner_id: str,
        *,
        alive_fn: Callable[[ContainerHandle], Awaitable[bool]],
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> ContainerHandle | None:
        """Return the cached container for *owner_id* if it still runs.

        Returns:
            The warm handle, or ``None`` when there is none or the one
            there is has died (in which case it has been evicted and the
            caller should create a fresh one).
        """
        async with self._lock:
            self._cancel_timer(owner_id)
            self._cancel_idle_timer(owner_id)
            handle = self._containers.get(owner_id)
            if handle is None:
                return None
            self._last_used[owner_id] = self._clock.monotonic()

        # Probed outside the lock: it is a round-trip to the container
        # backend, and holding the lock across it would serialise every
        # acquire in the process behind one inspect.
        alive = await probe_alive(
            handle,
            strategy="per-agent",
            owner_id=owner_id,
            alive_fn=alive_fn,
        )
        if alive:
            logger.info(
                SANDBOX_LIFECYCLE_ACQUIRE,
                strategy="per-agent",
                owner_id=owner_id,
                reused=True,
                container_id=handle.container_id,
            )
            return handle

        log_stale(handle, strategy="per-agent", owner_id=owner_id)
        await self._evict(owner_id, handle, destroy_fn=destroy_fn)
        return None

    async def acquire(
        self,
        *,
        owner_id: str,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
        alive_fn: Callable[[ContainerHandle], Awaitable[bool]],
    ) -> ContainerHandle:
        """Return an existing LIVE container or create a new one.

        Args:
            owner_id: Opaque identifier for the lifecycle owner.
            create_fn: Async factory that creates a fresh container.
            destroy_fn: Async callback to stop and remove the losing
                handle when a concurrent first-acquire races for the
                same owner.  Recorded for ``owner_id`` so a later
                ``release`` / timer teardown destroys the warm container
                even if no explicit ``release`` ran first.
            alive_fn: Probe deciding whether the cached handle is still
                usable.  Consulted on every cache hit.

        Returns:
            A ``ContainerHandle`` ready for command execution.
        """
        reusable = await self._reusable_handle(
            owner_id,
            alive_fn=alive_fn,
            destroy_fn=destroy_fn,
        )
        if reusable is not None:
            return reusable

        # Release the lock while creating (create_fn may be slow).
        handle = await create_fn()

        loser: ContainerHandle | None = None

        async with self._lock:
            # Record the destroy callback up front so the loser path
            # (and any later release/timer teardown) always has one,
            # even when no explicit release ran before this acquire.
            self._destroy_fns[owner_id] = destroy_fn
            # Re-check: a concurrent acquire may have won the race.
            existing = self._containers.get(owner_id)
            if existing is not None:
                # Cancel any grace/idle timers from an interleaved release.
                self._cancel_timer(owner_id)
                self._cancel_idle_timer(owner_id)
                loser = handle
            else:
                self._containers[owner_id] = handle
            self._last_used[owner_id] = self._clock.monotonic()
            logger.info(
                SANDBOX_LIFECYCLE_ACQUIRE,
                strategy="per-agent",
                owner_id=owner_id,
                reused=existing is not None,
                container_id=(existing or handle).container_id,
            )

        # Destroy the losing handle outside the lock, through the same reaper
        # the eviction path uses: a container this acquire created and lost is
        # discarded for the same reason and must not fail the caller either.
        if loser is not None:
            await reap(
                loser,
                strategy="per-agent",
                owner_id=owner_id,
                destroy_fn=destroy_fn,
            )

        return existing if existing is not None else handle

    async def release(
        self,
        *,
        owner_id: str,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Start a grace-period timer; destroy after expiry.

        Args:
            owner_id: The same identifier passed to ``acquire``.
            destroy_fn: Async callback to stop and remove the container.
        """
        async with self._lock:
            if owner_id not in self._containers:
                return

            self._cancel_timer(owner_id)
            self._destroy_fns[owner_id] = destroy_fn
            self._last_used[owner_id] = self._clock.monotonic()
            self._reset_idle_timer(owner_id)
            logger.info(
                SANDBOX_LIFECYCLE_RELEASE,
                strategy="per-agent",
                owner_id=owner_id,
                action="grace-start",
                grace_seconds=self._grace_seconds,
            )

            async def _grace_expire() -> None:
                """Tear down the per-agent container once the grace period elapses.

                Waits out a live background job first: a container the pin
                check reports live is not popped here at all, so a
                concurrent ``acquire()`` still finds it cached and this
                task's own cancellation (via ``_cancel_timer``) still works
                normally while the wait is in progress.
                """
                await self._clock.sleep(self._grace_seconds)
                handle = await self._await_unpinned(owner_id)
                if handle is None:
                    return
                async with self._lock:
                    handle = self._containers.pop(owner_id, None)
                    self._last_used.pop(owner_id, None)
                    self._timers.pop(owner_id, None)
                    self._destroy_fns.pop(owner_id, None)
                    idle = self._idle_timers.pop(owner_id, None)
                    if idle is not None and not idle.done():
                        idle.cancel()
                if handle is not None:
                    logger.info(
                        SANDBOX_LIFECYCLE_GRACE_EXPIRED,
                        strategy="per-agent",
                        owner_id=owner_id,
                        container_id=handle.container_id,
                    )
                    try:
                        await destroy_fn(handle)
                    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(exc)
                        logger.warning(
                            SANDBOX_LIFECYCLE_DESTROY_FAILED,
                            strategy="per-agent",
                            owner_id=owner_id,
                            container_id=handle.container_id,
                            error_type=type(exc).__name__,
                            error=safe_error_description(exc),
                        )

            self._timers[owner_id] = asyncio.create_task(
                _grace_expire(),
                name=f"sandbox-grace-{owner_id}",
            )

    async def cleanup_all(
        self,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Cancel all timers, destroy all containers.

        Args:
            destroy_fn: Async callback to stop and remove each container.
        """
        async with self._lock:
            all_tasks = list(self._timers.values()) + list(
                self._idle_timers.values(),
            )
            self._timers.clear()
            self._idle_timers.clear()
            self._destroy_fns.clear()

            for task in all_tasks:
                task.cancel()
            if all_tasks:
                await asyncio.gather(
                    *all_tasks,
                    return_exceptions=True,
                )

            handles = list(self._containers.values())
            count = len(handles)
            self._containers.clear()
            self._last_used.clear()

        for handle in handles:
            try:
                await destroy_fn(handle)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    SANDBOX_LIFECYCLE_DESTROY_FAILED,
                    strategy="per-agent",
                    container_id=handle.container_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        logger.info(
            SANDBOX_LIFECYCLE_CLEANUP,
            strategy="per-agent",
            destroyed_count=count,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cancel_timer(self, owner_id: str) -> None:
        """Cancel a pending grace timer (must hold ``_lock``)."""
        timer = self._timers.pop(owner_id, None)
        if timer is not None and not timer.done():
            timer.cancel()

    def _cancel_idle_timer(self, owner_id: str) -> None:
        """Cancel a pending idle timer (must hold ``_lock``)."""
        timer = self._idle_timers.pop(owner_id, None)
        if timer is not None and not timer.done():
            timer.cancel()

    def _reset_idle_timer(self, owner_id: str) -> None:
        """Start or restart the idle timeout timer (must hold ``_lock``)."""
        old = self._idle_timers.pop(owner_id, None)
        if old is not None and not old.done():
            old.cancel()
        if self._max_idle <= 0:
            return

        async def _idle_expire() -> None:
            """Tear the container down once ``_max_idle`` of inactivity elapses."""
            # Per-owner pump: exits naturally when the owner is
            # removed or idle is exceeded; cleanup_all() cancels it
            # on shutdown, so a cooperative _stop_event is not used.
            # lint-allow: long-running-loop-kill-switch -- per-owner; cleanup cancels.
            while True:
                async with self._lock:
                    last = self._last_used.get(owner_id)
                    if last is None or owner_id not in self._containers:
                        return
                    remaining = self._max_idle - (self._clock.monotonic() - last)
                if remaining <= 0:
                    break
                await self._clock.sleep(remaining)
            # Idle timeout reached. A live background job holds this off
            # exactly as it holds off grace expiry, via the same wait.
            handle = await self._await_unpinned(owner_id)
            if handle is None:
                return
            # Destroy.
            async with self._lock:
                handle = self._containers.pop(owner_id, None)
                self._last_used.pop(owner_id, None)
                self._idle_timers.pop(owner_id, None)
                destroy_fn = self._destroy_fns.pop(owner_id, None)
            if handle is not None and destroy_fn is not None:
                logger.info(
                    SANDBOX_LIFECYCLE_IDLE_EXPIRED,
                    strategy="per-agent",
                    owner_id=owner_id,
                    container_id=handle.container_id,
                )
                try:
                    await destroy_fn(handle)
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        SANDBOX_LIFECYCLE_DESTROY_FAILED,
                        strategy="per-agent",
                        owner_id=owner_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )

        self._idle_timers[owner_id] = asyncio.create_task(
            _idle_expire(),
            name=f"sandbox-idle-{owner_id}",
        )
