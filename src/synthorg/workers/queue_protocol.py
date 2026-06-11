"""Structural contract for the distributed task work-queue.

The concrete :class:`~synthorg.workers.claim.JetStreamTaskQueue` is the
``synthorg[distributed]`` extra: it lazy-imports ``nats`` and is exercised in
tests by an in-memory double. The dispatcher, worker, dead-letter, and heartbeat
consumers depend only on the queue's publish / pull / core-NATS surface, so they
annotate against this ``@runtime_checkable`` Protocol. Both the real client and
the test double satisfy it.

``nats`` message types (``Msg`` / ``Subscription``) appear only in signatures and
stay under ``TYPE_CHECKING`` so this module imports even when the extra is absent;
``runtime_checkable`` ``isinstance`` checks member presence, not signatures.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.workers.claim import TaskClaim

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription


@runtime_checkable
class TaskQueue(Protocol):
    """Publish, pull, and core-NATS surface of the distributed work-queue."""

    @property
    def is_running(self) -> bool:
        """Whether the queue client is connected and serving."""
        ...

    async def start(self) -> None:
        """Connect and provision the stream and consumer."""
        ...

    async def stop(self) -> None:
        """Drain and close the connection; idempotent."""
        ...

    async def publish_claim(self, claim: TaskClaim) -> None:
        """Enqueue a ready claim for workers to pull."""
        ...

    async def publish_dead(self, claim: TaskClaim) -> None:
        """Republish an exhausted claim to the dead-letter subject."""
        ...

    async def core_publish(self, subject: str, payload: bytes) -> None:
        """At-most-once publish on the core NATS connection (e.g. heartbeats)."""
        ...

    async def core_subscribe(
        self,
        subject: str,
        cb: Callable[[Msg], Awaitable[None]],
    ) -> Subscription:
        """Subscribe on the core NATS connection; returns the unsub handle."""
        ...

    async def next_claim(
        self,
        timeout: float,  # noqa: ASYNC109 -- mirrors JetStreamTaskQueue.next_claim
    ) -> tuple[TaskClaim, Msg] | None:
        """Fetch the next ready claim, or ``None`` on timeout."""
        ...

    async def next_dead(
        self,
        timeout: float,  # noqa: ASYNC109 -- mirrors JetStreamTaskQueue.next_dead
    ) -> tuple[TaskClaim, Msg] | None:
        """Fetch the next dead-letter claim, or ``None`` on timeout."""
        ...
