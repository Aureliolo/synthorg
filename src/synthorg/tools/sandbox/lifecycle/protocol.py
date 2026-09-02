"""Sandbox container lifecycle strategy protocol."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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
        """Validate invariants at construction time.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not self.container_id or self.container_id.isspace():
            msg = "container_id must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TrackedOwner:
    """An owner key a strategy holds, with the acquisition it is held under.

    The generation is what lets a release decided from a snapshot refuse
    itself once the snapshot is stale. A warm-cache reacquire hands back the
    SAME handle object, so identity cannot tell "still the run the sweep
    read as finished" from "reacquired and back in use"; the generation
    moves on every acquire and can.

    Attributes:
        key: The owner key exactly as the strategy holds it.
        generation: A number that changes on every acquire of this key and
            is never reused by a later one.
    """

    key: NotBlankStr
    generation: int


# 3 impls (PerAgent/PerTask/PerCall) + create_lifecycle_strategy
# config-discriminated factory.
@runtime_checkable
class SandboxLifecycleStrategy(Protocol):
    """Pluggable strategy for sandbox container creation and reuse.

    Implementations decide when to create new containers, when to reuse
    existing ones, and when to destroy them.  The strategy is decoupled
    from Docker internals via a ``create_fn`` callback.
    """

    @property
    def reuses_container(self) -> bool:
        """Whether containers outlive a single ``execute()`` call.

        ``False`` (``per-call``): the backend owns teardown and destroys
        the container immediately after each tool call.  ``True``
        (``per-agent`` / ``per-task``): the strategy owns teardown via
        ``release`` (grace / immediate), the idle timer, or
        ``cleanup_all``; the backend must NOT destroy the container in
        its own ``execute()`` finally block.
        """
        ...

    async def acquire(
        self,
        *,
        owner_id: str,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
        alive_fn: Callable[[ContainerHandle], Awaitable[bool]],
    ) -> ContainerHandle:
        """Get an existing container or create a new one for *owner_id*.

        Args:
            owner_id: Opaque identifier for the lifecycle owner (agent ID,
                task ID, or a per-call UUID).
            create_fn: Async factory that creates a fresh container.
            destroy_fn: Async callback that stops and removes a container
                (and its sidecar, if any).  A reuse strategy that loses a
                concurrent first-acquire race for *owner_id* uses this to
                tear down the extra handle immediately, so a parallel
                burst for one owner cannot leak warm containers.
            alive_fn: Async probe answering whether a handle's container is
                still running.  A reuse strategy MUST consult it before
                handing a cached handle back: a container can die between
                two tool calls for reasons the strategy never sees (a kill,
                a daemon restart, an out-of-memory), and a handle returned
                without asking makes every later command fail against a
                container that no longer exists, for the life of the
                process.  Keyword is required rather than defaulted for the
                same reason ``category`` is required on ``execute``: the
                safe-looking default is the one that silently reintroduces
                the fault.

        Returns:
            A ``ContainerHandle`` ready for command execution.
        """
        ...

    async def release(
        self,
        *,
        owner_id: str,
        destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
        expected_generation: int | None = None,
    ) -> None:
        """Signal that *owner_id* no longer needs its container.

        Depending on the strategy this may destroy the container
        immediately, start a grace-period timer, or do nothing.

        Args:
            owner_id: The same identifier passed to ``acquire``.
            destroy_fn: Async callback that stops and removes the
                container (and its sidecar, if any).
            expected_generation: The generation :meth:`tracked_owners`
                reported for this key when the caller decided to release
                it. A strategy that reuses containers refuses the release
                when the key has been acquired since, because the decision
                was made about a run that is no longer the one holding the
                container. ``None`` is the owner's own boundary release,
                which is about whatever is held now.
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

    async def tracked_owners(self) -> tuple[TrackedOwner, ...]:
        """The owner keys this strategy currently holds a container for.

        Read by the reclamation sweep, which asks of each owner whether its
        run has finished and hands the generation back on release; a
        strategy that reuses nothing answers empty.

        Returns:
            The keys with their generations, in no particular order.
        """
        ...
