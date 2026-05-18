"""In-memory ``JetStreamTaskQueue`` double for distributed-path tests.

This is a deliberate test double (per the CLAUDE.md test-double
ladder), NOT a ``MagicMock`` at a typed boundary. It models just
enough of the real JetStream work-queue behaviour to assert the
no-loss / no-duplication invariants the distributed path must hold,
without a broker:

- ``next_claim`` hands one ``(claim, raw)`` pair at a time, mirroring
  the real ``fetch(batch=1)`` plus timeout-returns-``None`` contract.
- A nacked claim is redelivered until ``max_deliver`` is reached; the
  final delivery's ``raw.metadata.num_delivered`` equals
  ``max_deliver`` so the worker's dead-letter trigger can detect it.
- A claim nacked on its final delivery is NOT redelivered (JetStream
  terminates it). The double captures nothing itself: a lost task is
  proven lost precisely because the double drops it, which is the
  failure mode the worker's ``publish_dead`` republish must close.
- ``publish_dead`` and ``core_publish`` record into inspectable lists
  so heartbeat + dead-letter assertions need no broker.

Timing (``ack_wait`` expiry) is intentionally NOT simulated here; the
real-NATS integration test owns that. These fast tests assert
deterministic logic invariants only.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from synthorg.workers.claim import TaskClaim


@dataclass
class FakeRawMetadata:
    """Mirror of ``nats`` message ``metadata`` (delivery bookkeeping)."""

    num_delivered: int


class FakeRawMessage:
    """Stand-in for a raw JetStream message.

    The real ``JetStreamTaskQueue.ack`` / ``nack`` / ``in_progress``
    static methods call ``raw.ack()`` / ``raw.nak()`` /
    ``raw.in_progress()``; this object records each so tests can assert
    the worker's finalize + ack-extension behaviour.
    """

    def __init__(
        self,
        *,
        claim: TaskClaim,
        num_delivered: int,
        queue: FakeJetStreamTaskQueue,
        kind: str = "ready",
    ) -> None:
        self._claim = claim
        self._queue = queue
        self._kind = kind
        self.metadata = FakeRawMetadata(num_delivered=num_delivered)
        self.ack_count = 0
        self.nak_count = 0
        self.in_progress_count = 0

    async def ack(self) -> None:
        self.ack_count += 1
        self._queue._record_ack(self._claim, self._kind)

    async def nak(self, delay: float = 0.0) -> None:
        self.nak_count += 1
        self._queue._record_nack(
            self._claim,
            self.metadata.num_delivered,
            self._kind,
        )

    async def in_progress(self) -> None:
        self.in_progress_count += 1
        self._queue.in_progress_total += 1


class _FakeNatsMsg:
    """Minimal raw core-NATS message (only ``.data`` is read)."""

    def __init__(self, data: bytes) -> None:
        self.data = data


class FakeSubscription:
    """Core-NATS subscription handle; records unsubscribe."""

    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeJetStreamTaskQueue:
    """Controllable in-memory work queue.

    Args:
        max_deliver: Redelivery ceiling, mirroring
            ``QueueConfig.max_deliver``. A claim nacked on delivery
            ``max_deliver`` is terminated (dropped, never republished
            by the queue itself).
    """

    def __init__(self, *, max_deliver: int = 3) -> None:
        self._max_deliver = max_deliver
        self._ready: asyncio.Queue[FakeRawMessage] = asyncio.Queue()
        self._dead_ready: asyncio.Queue[FakeRawMessage] = asyncio.Queue()
        self._running = True
        # Per-idempotency-key delivery counter so a redelivered claim
        # carries an increasing ``num_delivered`` like real JetStream.
        # Keyed by (kind, idempotency_key) so the ready and dead
        # consumers count deliveries independently, as two real durable
        # consumers do.
        self._delivery_count: dict[tuple[str, str], int] = {}
        # Inspection surfaces for assertions.
        self.acked: list[TaskClaim] = []
        self.terminated: list[TaskClaim] = []
        self.dead_acked: list[TaskClaim] = []
        self.dead_terminated: list[TaskClaim] = []
        self.dead_letters: list[TaskClaim] = []
        self.core_published: list[tuple[str, bytes]] = []
        self.in_progress_total = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def stop_running(self) -> None:
        """Flip ``is_running`` to ``False`` (dispatcher gating tests)."""
        self._running = False

    async def publish_claim(self, claim: TaskClaim) -> None:
        """Enqueue a fresh claim for first delivery."""
        self._enqueue(claim)

    async def publish_dead(self, claim: TaskClaim) -> None:
        """Record + enqueue a worker-republished dead-letter claim."""
        self.dead_letters.append(claim)
        self._enqueue(claim, kind="dead")

    async def core_publish(self, subject: str, payload: bytes) -> None:
        """Record a core-NATS (at-most-once) publish, e.g. heartbeats."""
        self.core_published.append((subject, payload))

    async def core_subscribe(
        self,
        subject: str,
        cb: Callable[[Any], Awaitable[None]],
    ) -> FakeSubscription:
        """Register a core-NATS subscription; returns an unsub handle."""
        self.subscribed_subject = subject
        self._core_cb = cb
        return FakeSubscription()

    async def deliver_heartbeat(self, payload: bytes) -> None:
        """Invoke the registered core-subscribe callback with *payload*."""
        cb = getattr(self, "_core_cb", None)
        if cb is None:
            msg = "no core subscription registered"
            raise RuntimeError(msg)
        await cb(_FakeNatsMsg(payload))

    def deliver_dead(self, claim: TaskClaim) -> None:
        """Directly enqueue a dead claim (drives DeadLetterConsumer)."""
        self._enqueue(claim, kind="dead")

    def _enqueue(self, claim: TaskClaim, *, kind: str = "ready") -> None:
        count_key = (kind, str(claim.idempotency_key))
        self._delivery_count[count_key] = self._delivery_count.get(count_key, 0) + 1
        raw = FakeRawMessage(
            claim=claim,
            num_delivered=self._delivery_count[count_key],
            queue=self,
            kind=kind,
        )
        target = self._dead_ready if kind == "dead" else self._ready
        target.put_nowait(raw)

    def _record_ack(self, claim: TaskClaim, kind: str) -> None:
        if kind == "dead":
            self.dead_acked.append(claim)
        else:
            self.acked.append(claim)

    def _record_nack(
        self,
        claim: TaskClaim,
        num_delivered: int,
        kind: str,
    ) -> None:
        if num_delivered >= self._max_deliver:
            # JetStream terminates a claim that exhausts max_deliver.
            # The double drops it: any task that ends up here without a
            # matching ``publish_dead`` is a proven silent loss.
            if kind == "dead":
                self.dead_terminated.append(claim)
            else:
                self.terminated.append(claim)
            return
        self._enqueue(claim, kind=kind)

    async def next_claim(
        self,
        timeout: float,  # noqa: ASYNC109 -- mirrors JetStreamTaskQueue.next_claim
    ) -> tuple[TaskClaim, FakeRawMessage] | None:
        """Hand the next ready claim, or ``None`` on timeout."""
        try:
            raw = await asyncio.wait_for(self._ready.get(), timeout=timeout)
        except TimeoutError:
            return None
        return raw._claim, raw

    async def next_dead(
        self,
        timeout: float,  # noqa: ASYNC109 -- mirrors JetStreamTaskQueue.next_dead
    ) -> tuple[TaskClaim, FakeRawMessage] | None:
        """Hand the next dead claim, or ``None`` on timeout."""
        try:
            raw = await asyncio.wait_for(
                self._dead_ready.get(),
                timeout=timeout,
            )
        except TimeoutError:
            return None
        return raw._claim, raw
