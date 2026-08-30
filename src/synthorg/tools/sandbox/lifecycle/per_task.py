"""Per-task sandbox lifecycle strategy.

Reuses a container for all tool calls within the same task.  On
``release()`` the container is destroyed immediately -- task boundaries
are clean cuts with no grace period -- unless a live background job is
still running in it, in which case release waits the job out exactly as
the per-agent strategy's grace/idle expiry do.
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
    SANDBOX_LIFECYCLE_RELEASE,
    SANDBOX_LIFECYCLE_TEARDOWN_PINNED,
)
from synthorg.tools.sandbox.lifecycle._liveness import log_stale, probe_alive, reap
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)

#: See ``per_agent.DEFAULT_PIN_RECHECK_SECONDS``; independent constant
#: rather than a shared import, because the two strategies are not
#: otherwise coupled and this one has no ``grace_period_seconds`` to
#: derive a sensible default from.
DEFAULT_PIN_RECHECK_SECONDS: Final[float] = 15.0


class PerTaskStrategy:
    """Reuse a container per *owner_id*, destroy immediately on release.

    A container whose ``pin_check`` reports a live background job is not
    torn down by ``release()``: it waits, polling at
    ``pin_recheck_seconds``, until the job ends. With no ``pin_check``
    wired (the default), ``release()`` destroys immediately exactly as
    it always has.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        pin_check: Callable[[str], Awaitable[bool]] | None = None,
        pin_recheck_seconds: float = DEFAULT_PIN_RECHECK_SECONDS,
    ) -> None:
        """Initialize the per-task lifecycle strategy.

        Args:
            clock: Time source for the pinned-release recheck wait.
                Defaults to ``SystemClock``; tests pass ``FakeClock``
                for deterministic recheck timing without real waiting.
                Unused unless ``pin_check`` is set.
            pin_check: Async predicate, keyed by ``container_id``,
                answering whether a live background job is still
                running inside it. ``None`` (the default) means no
                background-job feature is wired, so ``release()``
                behaves exactly as it always has.
            pin_recheck_seconds: How often a pinned container's release
                rechecks ``pin_check`` while held off. Unused when
                ``pin_check`` is ``None``.
        """
        self._containers: dict[str, ContainerHandle] = {}
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._pin_check = pin_check
        self._pin_recheck_seconds = pin_recheck_seconds
        self._pending_teardowns: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._shutting_down = False

    def bind_pin_check(self, pin_check: Callable[[str], Awaitable[bool]]) -> None:
        """Wire *pin_check* in after construction.

        See ``PerAgentStrategy.bind_pin_check`` for why this exists
        (the construction-order cycle between this strategy and the
        Docker sandbox whose bound method ``pin_check`` becomes). Bind
        before the first ``acquire()`` of a container that should be
        pinnable; ``release()`` only ever reads ``self._pin_check`` for
        a container already in ``self._containers``, so a bind before
        any container exists is race-free by construction.
        """
        self._pin_check = pin_check

    @property
    def reuses_container(self) -> bool:
        """``True`` -- one container per task, destroyed on release."""
        return True

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
            # A reacquire of the same owner must win over its own previous
            # release's deferred pinned-teardown: that task watches this
            # SAME cached handle, so leaving it running risks it observing
            # the pin clear later and destroying the container out from
            # under whoever just reacquired it, since identity alone
            # cannot distinguish "still the old, now-unpinned job" from
            # "reacquired and back in active use".
            self._cancel_pending_teardown(owner_id)
            handle = self._containers.get(owner_id)
        if handle is None:
            return None

        # Probed outside the lock: it is a round-trip to the container
        # backend, and holding the lock across it would serialise every
        # acquire in the process behind one inspect.
        alive = await probe_alive(
            handle,
            strategy="per-task",
            owner_id=owner_id,
            alive_fn=alive_fn,
        )
        if alive:
            logger.info(
                SANDBOX_LIFECYCLE_ACQUIRE,
                strategy="per-task",
                owner_id=owner_id,
                reused=True,
                container_id=handle.container_id,
            )
            return handle

        log_stale(handle, strategy="per-task", owner_id=owner_id)
        async with self._lock:
            # Identity-checked: a concurrent acquire may already have
            # replaced the entry, and evicting that one would destroy a
            # container somebody else is about to use.
            evicted = self._containers.get(owner_id) is handle
            if evicted:
                self._containers.pop(owner_id, None)
        # Teardown follows the eviction, not the observation. Two acquires can
        # read the same dead handle and both reach here, and a concurrent
        # `release` can take the entry from under both; reaping on the
        # observation would then destroy one container two or three times.
        # Exactly one caller wins the identity check, so exactly one tears it
        # down and the rest simply report the cache miss.
        if evicted:
            await reap(
                handle,
                strategy="per-task",
                owner_id=owner_id,
                destroy_fn=destroy_fn,
            )
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
            destroy_fn: Async callback to stop and remove the freshly
                created handle when a concurrent acquire won the race
                for the same owner, so the losing container is not
                leaked.  Also reaps a cached handle found dead.
            alive_fn: Probe deciding whether the cached handle is still
                usable.  Consulted on every cache hit.

        Returns:
            Result of type ``ContainerHandle``.
        """
        reusable = await self._reusable_handle(
            owner_id,
            alive_fn=alive_fn,
            destroy_fn=destroy_fn,
        )
        if reusable is not None:
            return reusable

        handle = await create_fn()

        loser: ContainerHandle | None = None
        async with self._lock:
            # Re-check: a concurrent acquire may have won the race.
            if owner_id in self._containers:
                logger.info(
                    SANDBOX_LIFECYCLE_ACQUIRE,
                    strategy="per-task",
                    owner_id=owner_id,
                    reused=True,
                )
                existing = self._containers[owner_id]
                loser = handle
            else:
                self._containers[owner_id] = handle
                logger.info(
                    SANDBOX_LIFECYCLE_ACQUIRE,
                    strategy="per-task",
                    owner_id=owner_id,
                    reused=False,
                    container_id=handle.container_id,
                )
                existing = handle

        # Destroy the losing handle outside the lock so a concurrent
        # first-acquire burst cannot leak the extra container.
        if loser is not None:
            try:
                await destroy_fn(loser)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    SANDBOX_LIFECYCLE_DESTROY_FAILED,
                    strategy="per-task",
                    owner_id=owner_id,
                    container_id=loser.container_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        return existing

    async def release(
        self,
        *,
        owner_id: str,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Destroy the container immediately, unless a live job pins it.

        A pinned container is not destroyed inline here: this call runs
        at the task boundary, and blocking it for as long as a
        background job keeps running would hold up whatever is awaiting
        task completion. Instead a background task waits the job out and
        destroys once unpinned -- the same shape per-agent's grace/idle
        expiry already use, just triggered at release instead of on a
        timer.
        """
        async with self._lock:
            handle = self._containers.get(owner_id)
        if handle is None:
            return

        pinned = self._pin_check is not None and await self._pin_check(
            handle.container_id
        )
        if pinned:
            await self._start_deferred_teardown(owner_id, destroy_fn=destroy_fn)
            return

        async with self._lock:
            # Identity-checked: a concurrent acquire may already have
            # replaced or removed the entry while the pin check above
            # was in flight, and popping that would destroy a container
            # somebody else is about to use (or double-destroy one this
            # call already tore down via a deferred teardown).
            if self._containers.get(owner_id) is not handle:
                return
            self._containers.pop(owner_id, None)
        logger.info(
            SANDBOX_LIFECYCLE_RELEASE,
            strategy="per-task",
            owner_id=owner_id,
            action="destroy",
            container_id=handle.container_id,
        )
        try:
            await destroy_fn(handle)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # The handle was already popped above; reinstate it (without
            # clobbering a concurrent re-acquire) so a live container
            # stays tracked and cleanup_all() can retry destruction
            # instead of orphaning it.
            async with self._lock:
                self._containers.setdefault(owner_id, handle)
            logger.warning(
                SANDBOX_LIFECYCLE_DESTROY_FAILED,
                strategy="per-task",
                owner_id=owner_id,
                container_id=handle.container_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _cancel_pending_teardown(self, owner_id: str) -> None:
        """Cancel a deferred pinned-release teardown (must hold ``_lock``)."""
        task = self._pending_teardowns.pop(owner_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _start_deferred_teardown(
        self,
        owner_id: str,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Launch the wait-then-destroy task for a pinned release.

        Tracked in ``_pending_teardowns`` so ``cleanup_all()`` can
        cancel and await it on shutdown instead of leaving it to finish
        (or not) after the strategy itself has torn everything else
        down.
        """

        async def _wait_then_destroy() -> None:
            # Upper-bounded by pin_check's own self-cleaning expiry;
            # tracked in _pending_teardowns so cleanup_all() cancels it.
            # lint-allow: long-running-loop-kill-switch -- bounded by
            # pin_check expiry; cleanup cancels.
            while True:
                async with self._lock:
                    handle = self._containers.get(owner_id)
                if handle is None:
                    return
                if self._pin_check is None or not await self._pin_check(
                    handle.container_id
                ):
                    break
                logger.debug(
                    SANDBOX_LIFECYCLE_TEARDOWN_PINNED,
                    strategy="per-task",
                    owner_id=owner_id,
                    container_id=handle.container_id,
                )
                await self._clock.sleep(self._pin_recheck_seconds)
            async with self._lock:
                self._pending_teardowns.pop(owner_id, None)
                if self._containers.get(owner_id) is not handle:
                    return
                self._containers.pop(owner_id, None)
            logger.info(
                SANDBOX_LIFECYCLE_RELEASE,
                strategy="per-task",
                owner_id=owner_id,
                action="destroy",
                container_id=handle.container_id,
            )
            try:
                await destroy_fn(handle)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                async with self._lock:
                    self._containers.setdefault(owner_id, handle)
                logger.warning(
                    SANDBOX_LIFECYCLE_DESTROY_FAILED,
                    strategy="per-task",
                    owner_id=owner_id,
                    container_id=handle.container_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        async with self._lock:
            if self._shutting_down:
                # cleanup_all() has already swept (or is concurrently
                # sweeping) every container still in `_containers`,
                # including this one if it's still there. A task
                # registered past that point would never be tracked in
                # `_pending_teardowns` for cleanup_all() to cancel, and
                # would keep polling `pin_check` against a container
                # shutdown may already have destroyed.
                logger.debug(
                    SANDBOX_LIFECYCLE_RELEASE,
                    strategy="per-task",
                    owner_id=owner_id,
                    action="deferred-pinned-skipped-shutdown",
                )
                return
            logger.info(
                SANDBOX_LIFECYCLE_RELEASE,
                strategy="per-task",
                owner_id=owner_id,
                action="deferred-pinned",
            )
            self._cancel_pending_teardown(owner_id)
            self._pending_teardowns[owner_id] = asyncio.create_task(
                _wait_then_destroy(),
                name=f"sandbox-pinned-release-{owner_id}",
            )

    async def cleanup_all(
        self,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Destroy all tracked containers."""
        async with self._lock:
            # Setting the flag in the SAME lock acquisition that snapshots
            # both dicts closes the window `_start_deferred_teardown` would
            # otherwise race through: it also checks `_shutting_down` under
            # this lock before registering, so a release racing this call
            # either lands its task in `pending` below (cancelled with the
            # rest) or sees the flag and never registers one at all -- no
            # third outcome where a task exists untracked.
            self._shutting_down = True
            pending = list(self._pending_teardowns.values())
            self._pending_teardowns.clear()
            handles = list(self._containers.values())
            count = len(handles)
            self._containers.clear()

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for handle in handles:
            try:
                await destroy_fn(handle)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    SANDBOX_LIFECYCLE_DESTROY_FAILED,
                    strategy="per-task",
                    container_id=handle.container_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        logger.info(
            SANDBOX_LIFECYCLE_CLEANUP,
            strategy="per-task",
            destroyed_count=count,
        )
