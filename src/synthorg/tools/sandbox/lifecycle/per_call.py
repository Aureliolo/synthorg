"""Per-call sandbox lifecycle strategy.

Wraps the current ephemeral behaviour: every ``acquire()`` creates a
fresh container, and ``release()`` is a no-op because the caller
(``DockerSandbox``) destroys the container in its own finally block.
"""

from collections.abc import Awaitable, Callable

from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_ACQUIRE,
    SANDBOX_LIFECYCLE_CLEANUP,
    SANDBOX_LIFECYCLE_RELEASE,
)
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)


class PerCallStrategy:
    """Create a new container for every ``execute()`` call."""

    @property
    def reuses_container(self) -> bool:
        """``False`` -- the backend destroys the container per call."""
        return False

    async def acquire(
        self,
        *,
        owner_id: str,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],  # noqa: ARG002
    ) -> ContainerHandle:
        """Create a fresh container (no reuse; nothing to lose).

        Returns:
            Result of type ``ContainerHandle``.
        """
        handle = await create_fn()
        logger.info(
            SANDBOX_LIFECYCLE_ACQUIRE,
            strategy="per-call",
            owner_id=owner_id,
            reused=False,
            container_id=handle.container_id,
        )
        return handle

    async def release(
        self,
        *,
        owner_id: str,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],  # noqa: ARG002
    ) -> None:
        """No-op -- the caller destroys the container."""
        logger.info(
            SANDBOX_LIFECYCLE_RELEASE,
            strategy="per-call",
            owner_id=owner_id,
            action="noop",
        )

    async def cleanup_all(
        self,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],  # noqa: ARG002
    ) -> None:
        """No-op -- nothing tracked."""
        logger.info(
            SANDBOX_LIFECYCLE_CLEANUP,
            strategy="per-call",
            action="noop",
        )
