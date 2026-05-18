"""Backend-side dead-letter consumer.

JetStream ``WorkQueuePolicy`` does NOT route to a dead subject when a
claim exhausts ``max_deliver``; it terminates the message. The worker
therefore republishes an exhausted claim to
``<dead_subject_prefix>.<task_id>`` (see
:meth:`synthorg.workers.worker.Worker._dead_letter`). This consumer
drains that subject and drives the task to ``FAILED`` so an exhausted
task is never silently lost, closing the no-loss gap the Distributed
Runtime design page describes.

Single-writer invariant: this consumer runs inside the backend
process, so it transitions the task through the injected ``TaskFailer``
seam, which goes through the normal ``TaskEngine`` mutation queue
(exactly the single-writer path the in-process dispatcher uses). It
never writes persistence directly.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.core.workers_errors import WorkerDeadLetterError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_DEAD_LETTER_ALREADY_TERMINAL,
    WORKERS_DEAD_LETTER_CONSUMER_STARTED,
    WORKERS_DEAD_LETTER_CONSUMER_STOPPED,
    WORKERS_DEAD_LETTER_DUPLICATE_SUPPRESSED,
    WORKERS_DEAD_LETTER_FAILED,
    WORKERS_DEAD_LETTER_TRANSITIONED,
)
from synthorg.workers.claim import JetStreamTaskQueue, TaskClaim
from synthorg.workers.config import QueueConfig  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository

logger = get_logger(__name__)

_DEAD_POLL_SECONDS: Final[float] = 1.0
"""Per-fetch timeout on the dead-subject consumer.

Short so ``stop()`` is not blocked behind an idle fetch for the full
ack window, mirroring the worker's ``_MAX_FETCH_POLL_SECONDS``."""

_DEDUP_TTL_SAFETY_MULTIPLIER: Final[float] = 2.0
"""Same horizon multiplier as the worker's dedup TTL: the dead-claim
dedup row must outlive the dead consumer's own redelivery window."""


class DeadLetterOutcome(StrEnum):
    """Result of attempting to fail a dead-lettered task."""

    TRANSITIONED = "transitioned"
    ALREADY_TERMINAL = "already_terminal"
    NOT_FOUND = "not_found"
    RETRYABLE = "retryable"


TaskFailer = Callable[[str, str], Awaitable[DeadLetterOutcome]]
"""Seam that transitions a task to FAILED.

Args are ``(task_id, reason)``. The production implementation
(:func:`make_engine_task_failer`) routes through
``TaskEngine.transition_task`` so the single-writer invariant holds;
tests inject a fake.
"""


def make_engine_task_failer(engine: Any) -> TaskFailer:
    """Build a :data:`TaskFailer` backed by a ``TaskEngine``.

    Maps engine exceptions onto :class:`DeadLetterOutcome` so the
    consumer can decide ack vs nack without coupling to engine
    internals. Engine error types are imported lazily to keep this
    module importable without the engine package.
    """
    from synthorg.core.enums import TaskStatus  # noqa: PLC0415
    from synthorg.engine.errors import (  # noqa: PLC0415
        TaskEngineNotRunningError,
        TaskEngineQueueFullError,
        TaskMutationError,
        TaskNotFoundError,
    )

    async def _fail(task_id: str, reason: str) -> DeadLetterOutcome:
        try:
            await engine.transition_task(
                task_id,
                TaskStatus.FAILED,
                requested_by="dead-letter-consumer",
                reason=reason,
            )
        except TaskNotFoundError:
            # Subclass of TaskMutationError, so it MUST be caught first.
            return DeadLetterOutcome.NOT_FOUND
        except TaskEngineNotRunningError, TaskEngineQueueFullError:
            # Engine transiently unavailable: redeliver and retry.
            return DeadLetterOutcome.RETRYABLE
        except TaskMutationError:
            # Invalid transition: the task is already in a terminal /
            # known state (e.g. COMPLETED, CANCELLED, already FAILED).
            # Not a loss -- the task is accounted for -- so treat as a
            # no-op rather than retrying forever.
            return DeadLetterOutcome.ALREADY_TERMINAL
        return DeadLetterOutcome.TRANSITIONED

    return _fail


class DeadLetterConsumer:
    """Drains the dead subject and fails exhausted tasks.

    Args:
        task_queue: Connected :class:`JetStreamTaskQueue` (shared with
            the dispatcher in the backend process).
        task_failer: Seam transitioning a task to FAILED.
        queue_config: Queue config (ack wait / max deliver) used for
            the dedup TTL and final-delivery detection.
        seen_claims: Durable dedup repository so a redelivered dead
            message does not double-transition. ``None`` disables
            dedup (legacy / no-persistence test paths).
        clock: Clock seam for the dedup row timestamps.
    """

    def __init__(
        self,
        *,
        task_queue: JetStreamTaskQueue,
        task_failer: TaskFailer,
        queue_config: QueueConfig,
        seen_claims: SeenClaimsRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._task_failer = task_failer
        self._queue_config = queue_config
        self._seen_claims = seen_claims
        self._clock: Clock = clock or SystemClock()
        self._dedup_ttl_seconds: float = (
            float(queue_config.ack_wait_seconds)
            * float(queue_config.max_deliver)
            * _DEDUP_TTL_SAFETY_MULTIPLIER
        )
        self._running = False
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see Worker
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- ctx
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Whether the consume loop is active."""
        return self._running

    async def start(self) -> None:
        """Spawn the background consume loop. Non-blocking.

        Raises:
            RuntimeError: If already running (a second loop on the same
                dead consumer would double-process every claim).
        """
        async with self._lifecycle_lock:
            if self._running:
                msg = "DeadLetterConsumer is already running"
                raise RuntimeError(msg)
            self._running = True
            self._stop_event.clear()
            self._task = asyncio.create_task(self._consume_loop())
            logger.info(WORKERS_DEAD_LETTER_CONSUMER_STARTED)

    async def stop(self) -> None:
        """Stop the consume loop and await its exit. Idempotent."""
        async with self._lifecycle_lock:
            if not self._running:
                return
            self._stop_event.set()
            task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        async with self._lifecycle_lock:
            self._running = False
            self._task = None
            logger.info(WORKERS_DEAD_LETTER_CONSUMER_STOPPED)

    async def _consume_loop(self) -> None:
        """Fetch and handle dead claims until stopped."""
        # lint-allow: long-running-loop-kill-switch -- _stop_event drives shutdown.
        while not self._stop_event.is_set():
            pair = await self._task_queue.next_dead(timeout=_DEAD_POLL_SECONDS)
            if pair is None:
                continue
            claim, raw = pair
            await self._handle(claim, raw)

    async def _handle(self, claim: TaskClaim, raw: Any) -> None:
        """Drive one dead claim to FAILED, idempotently."""
        if await self._already_handled(claim):
            await JetStreamTaskQueue.ack(raw)
            return
        try:
            outcome = await self._task_failer(
                str(claim.task_id),
                "Task exhausted distributed retry budget (dead-lettered)",
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # An unmapped failure means we cannot prove the task was
            # failed; raising loudly beats acking and losing it.
            logger.error(
                WORKERS_DEAD_LETTER_FAILED,
                task_id=claim.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Dead-letter handling failed for task {claim.task_id}"
            raise WorkerDeadLetterError(msg) from exc

        if outcome is DeadLetterOutcome.RETRYABLE:
            await self._handle_retryable(claim, raw)
            return
        if outcome is DeadLetterOutcome.TRANSITIONED:
            logger.warning(
                WORKERS_DEAD_LETTER_TRANSITIONED,
                task_id=claim.task_id,
                idempotency_key=claim.idempotency_key,
            )
        elif outcome is DeadLetterOutcome.ALREADY_TERMINAL:
            logger.info(
                WORKERS_DEAD_LETTER_ALREADY_TERMINAL,
                task_id=claim.task_id,
            )
        # NOT_FOUND falls through: the task is gone, nothing to fail.
        await self._mark_handled(claim)
        await JetStreamTaskQueue.ack(raw)

    async def _handle_retryable(self, claim: TaskClaim, raw: Any) -> None:
        """Nack a retryable dead claim, or fail loudly when exhausted.

        If the engine is transiently unavailable, redeliver. If the
        dead consumer's own delivery budget is exhausted, raising is
        the honest outcome: silently acking would lose the task, which
        is exactly what this consumer exists to prevent.
        """
        metadata = getattr(raw, "metadata", None)
        num_delivered = int(getattr(metadata, "num_delivered", 0))
        if num_delivered >= self._queue_config.max_deliver:
            logger.error(
                WORKERS_DEAD_LETTER_FAILED,
                task_id=claim.task_id,
                reason="retryable_exhausted",
                num_delivered=num_delivered,
            )
            msg = (
                f"Dead-letter task {claim.task_id} could not be failed "
                "before the dead consumer exhausted its delivery budget"
            )
            raise WorkerDeadLetterError(msg)
        await JetStreamTaskQueue.nack(raw)

    async def _already_handled(self, claim: TaskClaim) -> bool:
        """Return ``True`` if this dead claim was already processed.

        Fail-open on a transient lookup error (mirrors the worker): a
        DB hiccup must not wedge the dead consumer; the redelivery
        re-checks.
        """
        if self._seen_claims is None:
            return False
        try:
            seen = await self._seen_claims.is_completed(
                idempotency_key=NotBlankStr(claim.idempotency_key),
            )
        except QueryError as exc:
            logger.warning(
                WORKERS_DEAD_LETTER_FAILED,
                task_id=claim.task_id,
                reason="dedup_lookup_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if seen:
            logger.info(
                WORKERS_DEAD_LETTER_DUPLICATE_SUPPRESSED,
                task_id=claim.task_id,
                idempotency_key=claim.idempotency_key,
            )
        return seen

    async def _mark_handled(self, claim: TaskClaim) -> None:
        """Record the dead claim so a redelivery ack-skips. Fail-open."""
        if self._seen_claims is None:
            return
        try:
            await self._seen_claims.mark_seen(
                idempotency_key=NotBlankStr(claim.idempotency_key),
                claim_id=NotBlankStr(claim.task_id),
                now=self._clock.now(),
                ttl_seconds=self._dedup_ttl_seconds,
            )
        except QueryError as exc:
            logger.warning(
                WORKERS_DEAD_LETTER_FAILED,
                task_id=claim.task_id,
                reason="dedup_mark_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
