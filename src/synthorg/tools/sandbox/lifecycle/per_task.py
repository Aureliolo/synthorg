"""Per-task sandbox lifecycle strategy.

Reuses a container for all tool calls within the same task.  On
``release()`` the container is destroyed immediately -- task boundaries
are clean cuts with no grace period.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_ACQUIRE,
    SANDBOX_LIFECYCLE_CLEANUP,
    SANDBOX_LIFECYCLE_DESTROY_FAILED,
    SANDBOX_LIFECYCLE_RELEASE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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

    async def acquire(
        self,
        *,
        owner_id: str,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> ContainerHandle:
        """Return an existing container or create a new one.

        Args:
            owner_id: Opaque identifier for the lifecycle owner.
            create_fn: Async factory that creates a fresh container.
            destroy_fn: Async callback to stop and remove the freshly
                created handle when a concurrent acquire won the race
                for the same owner, so the losing container is not
                leaked.
        """
        async with self._lock:
            if owner_id in self._containers:
                logger.info(
                    SANDBOX_LIFECYCLE_ACQUIRE,
                    strategy="per-task",
                    owner_id=owner_id,
                    reused=True,
                )
                return self._containers[owner_id]

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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
