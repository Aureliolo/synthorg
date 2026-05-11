"""Sandbox container lifecycle strategy protocol."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.core.types import NotBlankStr


@dataclass(frozen=True, slots=True)
class ContainerHandle:
    """Opaque handle to a running sandbox container and its optional sidecar.

    Attributes:
        container_id: Docker container ID for the sandbox.
        sidecar_id: Docker container ID for the network sidecar, or ``None``
            when no sidecar was created.
        network_mode: Docker network mode for commands executing in this
            container (e.g. ``"container:<sidecar_id>"`` or ``"none"``).
    """

    container_id: NotBlankStr
    sidecar_id: NotBlankStr | None = None
    network_mode: str = "none"

    def __post_init__(self) -> None:
        """Validate invariants at construction time."""
        if not self.container_id or self.container_id.isspace():
            msg = "container_id must be non-empty"
            raise ValueError(msg)


# audit-keep 2026-05-10: 3 impls (PerAgent/PerTask/PerCall) +
# create_lifecycle_strategy config-discriminated factory.
@runtime_checkable
class SandboxLifecycleStrategy(Protocol):
    """Pluggable strategy for sandbox container creation and reuse.

    Implementations decide when to create new containers, when to reuse
    existing ones, and when to destroy them.  The strategy is decoupled
    from Docker internals via a ``create_fn`` callback.
    """

    async def acquire(
        self,
        *,
        owner_id: str,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
    ) -> ContainerHandle:
        """Get an existing container or create a new one for *owner_id*.

        Args:
            owner_id: Opaque identifier for the lifecycle owner (agent ID,
                task ID, or a per-call UUID).
            create_fn: Async factory that creates a fresh container.

        Returns:
            A ``ContainerHandle`` ready for command execution.
        """
        ...

    async def release(
        self,
        *,
        owner_id: str,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Signal that *owner_id* no longer needs its container.

        Depending on the strategy this may destroy the container
        immediately, start a grace-period timer, or do nothing.

        Args:
            owner_id: The same identifier passed to ``acquire``.
            destroy_fn: Async callback that stops and removes the
                container (and its sidecar, if any).
        """
        ...

    async def cleanup_all(
        self,
        *,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
    ) -> None:
        """Destroy all tracked containers.

        Called during backend shutdown to ensure no containers leak.

        Args:
            destroy_fn: Async callback that stops and removes a
                container (and its sidecar, if any).
        """
        ...
