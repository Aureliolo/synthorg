"""Per-agent sandbox lifecycle strategy.

Reuses a container for all tool calls by the same agent.  After
``release()`` the container is kept alive for a configurable grace
period, then destroyed.  A subsequent ``acquire()`` within the grace
window cancels the timer and returns the warm container.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_ACQUIRE,
    SANDBOX_LIFECYCLE_CLEANUP,
    SANDBOX_LIFECYCLE_DESTROY_FAILED,
    SANDBOX_LIFECYCLE_GRACE_EXPIRED,
    SANDBOX_LIFECYCLE_IDLE_EXPIRED,
    SANDBOX_LIFECYCLE_RELEASE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
    from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)


class PerAgentStrategy:
    """Reuse a container per *owner_id*, destroy after grace period."""

    def __init__(
        self,
        config: SandboxLifecycleConfig,
        *,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the per-agent lifecycle strategy.

        Args:
            config: Lifecycle configuration (grace period, max idle, etc.).
            clock: Time source for grace + idle timers. Defaults to
                ``SystemClock``; tests pass ``FakeClock`` for
                deterministic timer expiry without real waiting.
        """
        self._grace_seconds = config.grace_period_seconds
        self._max_idle = config.max_idle_seconds
        self._clock: Clock = clock or SystemClock()
        self._containers: dict[str, ContainerHandle] = {}
        self._last_used: dict[str, float] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._idle_timers: dict[str, asyncio.Task[None]] = {}
        self._destroy_fns: dict[str, Callable[[ContainerHandle], Awaitable[None]]] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        owner_id: str,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
    ) -> ContainerHandle:
        """Return an existing container or create a new one.

        Args:
            owner_id: Opaque identifier for the lifecycle owner.
            create_fn: Async factory that creates a fresh container.

        Returns:
            A ``ContainerHandle`` ready for command execution.
        """
        async with self._lock:
            self._cancel_timer(owner_id)
            self._cancel_idle_timer(owner_id)

            if owner_id in self._containers:
                logger.info(
                    SANDBOX_LIFECYCLE_ACQUIRE,
                    strategy="per-agent",
                    owner_id=owner_id,
                    reused=True,
                )
                self._last_used[owner_id] = self._clock.monotonic()
                return self._containers[owner_id]

        # Release the lock while creating (create_fn may be slow).
        handle = await create_fn()

        loser: ContainerHandle | None = None
        loser_destroy: Callable[[ContainerHandle], Awaitable[None]] | None = None

        async with self._lock:
            # Re-check: a concurrent acquire may have won the race.
            if owner_id in self._containers:
                existing = self._containers[owner_id]
                # Cancel any grace/idle timers from an interleaved release.
                self._cancel_timer(owner_id)
                self._cancel_idle_timer(owner_id)
                logger.info(
                    SANDBOX_LIFECYCLE_ACQUIRE,
                    strategy="per-agent",
                    owner_id=owner_id,
                    reused=True,
                )
                self._last_used[owner_id] = self._clock.monotonic()
                loser = handle
                loser_destroy = self._destroy_fns.get(owner_id)
            else:
                existing = None
                self._containers[owner_id] = handle
                self._last_used[owner_id] = self._clock.monotonic()
                logger.info(
                    SANDBOX_LIFECYCLE_ACQUIRE,
                    strategy="per-agent",
                    owner_id=owner_id,
                    reused=False,
                    container_id=handle.container_id,
                )

        # Destroy the losing handle outside the lock.
        if loser is not None:
            if loser_destroy is not None:
                try:
                    await loser_destroy(loser)
                except Exception:
                    logger.warning(
                        SANDBOX_LIFECYCLE_DESTROY_FAILED,
                        strategy="per-agent",
                        owner_id=owner_id,
                        container_id=loser.container_id,
                    )
            else:
                logger.warning(
                    SANDBOX_LIFECYCLE_DESTROY_FAILED,
                    strategy="per-agent",
                    owner_id=owner_id,
                    container_id=loser.container_id,
                    reason="no destroy_fn available for losing handle",
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
                await self._clock.sleep(self._grace_seconds)
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
                    except Exception:
                        logger.warning(
                            SANDBOX_LIFECYCLE_DESTROY_FAILED,
                            strategy="per-agent",
                            owner_id=owner_id,
                            container_id=handle.container_id,
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
            except Exception:
                logger.warning(
                    SANDBOX_LIFECYCLE_DESTROY_FAILED,
                    strategy="per-agent",
                    container_id=handle.container_id,
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
            while True:
                async with self._lock:
                    last = self._last_used.get(owner_id)
                    if last is None or owner_id not in self._containers:
                        return
                    remaining = self._max_idle - (self._clock.monotonic() - last)
                if remaining <= 0:
                    break
                await self._clock.sleep(remaining)
            # Idle timeout reached -- destroy.
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
                except Exception:
                    logger.warning(
                        SANDBOX_LIFECYCLE_DESTROY_FAILED,
                        strategy="per-agent",
                        owner_id=owner_id,
                    )

        self._idle_timers[owner_id] = asyncio.create_task(
            _idle_expire(),
            name=f"sandbox-idle-{owner_id}",
        )
