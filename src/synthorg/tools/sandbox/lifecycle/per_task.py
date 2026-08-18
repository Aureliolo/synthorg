"""Per-task sandbox lifecycle strategy.

Reuses a container for all tool calls within the same task.  On
``release()`` the container is destroyed immediately -- task boundaries
are clean cuts with no grace period.
"""

import asyncio
from collections.abc import Awaitable, Callable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_ACQUIRE,
    SANDBOX_LIFECYCLE_CLEANUP,
    SANDBOX_LIFECYCLE_DESTROY_FAILED,
    SANDBOX_LIFECYCLE_RELEASE,
)
from synthorg.tools.sandbox.lifecycle._liveness import log_stale, probe_alive, reap
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)


class PerTaskStrategy:
    """Reuse a container per *owner_id*, destroy immediately on release."""

    def __init__(self) -> None:
        """Initialize the per-task lifecycle strategy."""
        self._containers: dict[str, ContainerHandle] = {}
        self._lock = asyncio.Lock()

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
        """Destroy the container immediately (task boundary)."""
        async with self._lock:
            handle = self._containers.pop(owner_id, None)
        if handle is None:
            return
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

    async def cleanup_all(
        self,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Destroy all tracked containers."""
        async with self._lock:
            handles = list(self._containers.values())
            count = len(handles)
            self._containers.clear()

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
