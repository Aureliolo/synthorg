"""Resource locking for parallel agent execution.

Provides exclusive access to shared resources (e.g. file paths) so that
concurrent agents do not clobber each other's work.

The ``ResourceLock`` protocol is pluggable; ``InMemoryResourceLock`` is
the default single-process implementation using ``asyncio.Lock``.
"""

import asyncio
from typing import Protocol, runtime_checkable

from synthorg.observability import get_logger
from synthorg.observability.events.parallel import (
    PARALLEL_LOCK_ACQUIRED,
    PARALLEL_LOCK_CONFLICT,
    PARALLEL_LOCK_RELEASED,
)

logger = get_logger(__name__)


@runtime_checkable
class ResourceLock(Protocol):
    """Protocol for exclusive resource locking.

    Resources are identified by string keys (typically file paths).
    Holders are identified by agent IDs.
    """

    async def acquire(self, resource: str, holder: str) -> bool:
        """Attempt to acquire exclusive access to *resource*.

        Returns ``True`` if the lock was acquired (or was already held
        by the same holder).  Returns ``False`` if another holder owns
        the lock.
        """
        ...

    async def release(self, resource: str, holder: str) -> None:
        """Release a lock on *resource*.

        No-op if the resource is not locked or is held by a different
        holder.
        """
        ...

    async def release_all(self, holder: str) -> int:
        """Release all locks held by *holder*.

        Returns the number of locks released.
        """
        ...

    def is_locked(self, resource: str) -> bool:
        """Return ``True`` if *resource* is currently locked."""
        ...

    def holder_of(self, resource: str) -> str | None:
        """Return the holder of *resource*, or ``None`` if unlocked."""
        ...


class InMemoryResourceLock:
    """In-memory resource lock using ``asyncio.Lock`` for mutual exclusion.

    Suitable for single-process deployments.  All mutations are guarded
    by an internal ``asyncio.Lock`` to ensure correctness under
    concurrent access from multiple ``asyncio.Task`` instances.
    """

    def __init__(self) -> None:
        self._locks: dict[str, str] = {}
        self._mutex = asyncio.Lock()

    async def acquire(self, resource: str, holder: str) -> bool:
        """Attempt to acquire exclusive access to *resource*."""
        async with self._mutex:
            current = self._locks.get(resource)
            if current is None:
                self._locks[resource] = holder
                logger.debug(
                    PARALLEL_LOCK_ACQUIRED,
                    resource=resource,
                    holder=holder,
                )
                return True
            if current == holder:
                return True
            logger.debug(
                PARALLEL_LOCK_CONFLICT,
                resource=resource,
                holder=holder,
                current_holder=current,
            )
            return False

    async def release(self, resource: str, holder: str) -> None:
        """Release a lock on *resource* if held by *holder*."""
        async with self._mutex:
            current = self._locks.get(resource)
            if current == holder:
                del self._locks[resource]
                logger.debug(
                    PARALLEL_LOCK_RELEASED,
                    resource=resource,
                    holder=holder,
                )
            elif current is not None:
                logger.warning(
                    PARALLEL_LOCK_CONFLICT,
                    resource=resource,
                    holder=holder,
                    current_holder=current,
                    error="Release attempted by non-holder",
                )

    async def release_all(self, holder: str) -> int:
        """Release all locks held by *holder*."""
        async with self._mutex:
            to_release = [r for r, h in self._locks.items() if h == holder]
            for resource in to_release:
                del self._locks[resource]
                logger.debug(
                    PARALLEL_LOCK_RELEASED,
                    resource=resource,
                    holder=holder,
                )
            return len(to_release)

    def is_locked(self, resource: str) -> bool:
        """Return ``True`` if *resource* is currently locked."""
        return resource in self._locks

    def holder_of(self, resource: str) -> str | None:
        """Return the holder of *resource*, or ``None``."""
        return self._locks.get(resource)
