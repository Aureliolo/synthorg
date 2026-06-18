"""Distributed worker: pulls claims, executes tasks, transitions via HTTP.

The worker is a separate Python process launched via
``synthorg worker start`` (Go CLI wrapper at ``cli/cmd/worker_start.go``).
It connects to NATS JetStream for claim delivery and to the backend
HTTP API for task transitions, preserving the ``TaskEngine``
single-writer invariant: workers never write to persistence directly.

The execution path is intentionally minimal in this initial
implementation: the worker fetches a claim, calls an injected
``executor`` callable with the claim's ``task_id``, and surfaces the
outcome back to the backend. The executor is the seam where future
work plugs in the real agent runtime; today it is a callable the
caller provides (typically ``synthorg.engine.agent_engine`` in a
follow-up PR).
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.workers import (
    WORKERS_ACK_EXTEND_FAILED,
    WORKERS_CLAIM_DEAD_LETTERED,
    WORKERS_CLAIM_RECEIVED,
    WORKERS_DEAD_LETTER_PUBLISH_FAILED,
    WORKERS_DEDUP_LOOKUP_FAILED,
    WORKERS_DEDUP_MARK_FAILED,
    WORKERS_DUPLICATE_CLAIM_SUPPRESSED,
    WORKERS_EXECUTOR_FAILED,
    WORKERS_FINALIZE_FAILED,
    WORKERS_HEARTBEAT_FAILED,
    WORKERS_HEARTBEAT_SENT,
    WORKERS_POOL_STARTED,
    WORKERS_POOL_STOP_FAILED,
    WORKERS_WORKER_START_REJECTED,
    WORKERS_WORKER_STARTED,
    WORKERS_WORKER_STOPPED,
)
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
from synthorg.workers.claim import (
    JetStreamTaskQueue,
    TaskClaim,
    TaskClaimStatus,
)
from synthorg.workers.config import QueueConfig
from synthorg.workers.heartbeat_models import (
    HEARTBEAT_SUBJECT_PREFIX,
    WorkerHeartbeat,
)

if TYPE_CHECKING:
    # nats-py is an optional dependency, so the raw-message type stays
    # guarded for clean import when it is absent; tests also drive the
    # worker with duck-typed message fakes.
    from nats.aio.msg import Msg

logger = get_logger(__name__)

_MAX_FETCH_POLL_SECONDS: Final[float] = 1.0
"""Internal constant by design: maximum seconds
:meth:`Worker._run_once` waits inside a single ``next_claim`` call.
Keeps ``stop()`` responsive -- even with a 300s ack deadline, the
claim loop wakes up at least once per second to check ``_stop_event``
rather than blocking for the full ``ack_wait`` window.  Not exposed
to the settings registry."""


TaskExecutor = Callable[[TaskClaim], Awaitable[TaskClaimStatus]]
"""Callable the worker invokes for each claim.

Takes a :class:`TaskClaim` and returns a terminal
:class:`TaskClaimStatus`. The executor is responsible for calling
the backend HTTP API to transition the task; the worker only handles
the claim ack/nack based on the returned status.
"""


_ACK_EXTEND_SAFETY_FRACTION: Final[float] = 0.5
"""Fraction of ``ack_wait`` that caps the working-ack cadence.

``heartbeat_interval_seconds`` is only validated to be strictly below
``ack_wait_seconds``. If an operator tunes it just under ``ack_wait``,
the sleep-first first extension could land after the deadline under
scheduler or broker jitter, allowing a duplicate redelivery. Capping
the ack-extension interval at half of ``ack_wait`` guarantees the
first extension fires with a full deadline of headroom regardless of
how the heartbeat interval is configured. The heartbeat *publish*
cadence is unaffected; only the ack-extension loop uses this cap."""


_DEDUP_TTL_SAFETY_MULTIPLIER: Final[float] = 2.0
"""Multiplier applied to ``ack_wait * max_deliver`` for the dedup TTL.

The dedup row must outlive the JetStream redelivery horizon
(``ack_wait * max_deliver``) by enough slack that a slow worker
finalising its ack just past the deadline still finds the row. The
2.0 multiplier gives that slack without keeping the table unbounded
in size."""


class Worker:
    """Single-process distributed worker.

    Args:
        queue_config: Queue configuration (ack wait, max deliver).
        task_queue: Connected :class:`JetStreamTaskQueue`.
        executor: Async callable invoked for each claim.
        worker_id: Identifier for logging + heartbeat subject.
        seen_claims: Durable dedup repository consulted before each
            claim is executed; protects against JetStream
            redeliveries (ack lost in transit, worker crash before
            ack). When ``None``, dedup is disabled (legacy behaviour;
            tests without a persistence backend rely on this).
        clock: Time source for the dedup row's ``seen_at`` /
            ``expires_at`` timestamps. Inject ``FakeClock`` in tests.
    """

    def __init__(  # noqa: PLR0913 -- canonical worker construction surface
        self,
        *,
        queue_config: QueueConfig,
        task_queue: JetStreamTaskQueue,
        executor: TaskExecutor,
        worker_id: str,
        seen_claims: SeenClaimsRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._queue_config = queue_config
        self._task_queue = task_queue
        self._executor = executor
        self._worker_id = worker_id
        self._seen_claims = seen_claims
        self._clock: Clock = clock or SystemClock()
        self._dedup_ttl_seconds: float = (
            float(queue_config.ack_wait_seconds)
            * float(queue_config.max_deliver)
            * _DEDUP_TTL_SAFETY_MULTIPLIER
        )
        self._heartbeat_interval: float = float(
            queue_config.heartbeat_interval_seconds,
        )
        self._ack_extend_interval: float = min(
            self._heartbeat_interval,
            float(queue_config.ack_wait_seconds) * _ACK_EXTEND_SAFETY_FRACTION,
        )
        self._claims_done = 0
        self._running = False
        # Eager init: ``stop()`` may set the event before ``run()`` has
        # ever entered the loop, so a half-published attribute would
        # race with shutdown signalling.
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see above.
        # Dedicated lifecycle lock per docs/reference/lifecycle-sync.md.
        # Held across the full body of run() and stop() so a racing
        # start cannot see _running=False mid-drain and spawn a new
        # claim loop that the outgoing stop never waits on.  Worker is
        # an "in-place runner" (start runs the loop on the calling
        # coroutine), so the lock guards only the _running transition;
        # holding it across the whole loop body would deadlock a
        # second concurrent caller. Eager init: stop() must be safe
        # before any run() call.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.

    @property
    def is_running(self) -> bool:
        """Whether the worker's claim loop is active."""
        return self._running

    async def run(self) -> None:
        """Run the claim loop until :meth:`stop` is called.

        Pulls one claim at a time, invokes the executor, and acks or
        nacks the JetStream message based on the executor's returned
        status.

        Raises:
            RuntimeError: When the worker is already running.
        """
        async with self._lifecycle_lock:
            if self._running:
                msg = f"Worker {self._worker_id} is already running"
                logger.warning(
                    WORKERS_WORKER_START_REJECTED,
                    worker_id=self._worker_id,
                    reason="already_running",
                    error_type=RuntimeError.__name__,
                )
                raise RuntimeError(msg)
            self._running = True
            self._stop_event.clear()
            logger.info(WORKERS_WORKER_STARTED, worker_id=self._worker_id)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            # lint-allow: long-running-loop-kill-switch -- _stop_event drives shutdown.
            while not self._stop_event.is_set():
                await self._run_once()
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            async with self._lifecycle_lock:
                self._running = False
                logger.info(WORKERS_WORKER_STOPPED, worker_id=self._worker_id)

    async def stop(self) -> None:
        """Signal the claim loop to exit after the current claim."""
        async with self._lifecycle_lock:
            self._stop_event.set()

    async def _run_once(self) -> None:
        """Fetch and process a single claim.

        Uses a short fetch timeout (:data:`_MAX_FETCH_POLL_SECONDS`)
        instead of the full ``ack_wait`` window so ``stop()`` is not
        blocked behind an idle JetStream fetch for the entire ack
        deadline. The loop's outer ``while not self._stop_event.is_set()``
        handles the stop signal; this method just returns eagerly on
        an empty fetch so the loop can observe it.

        Dedup is consulted as a read-only check BEFORE execution and
        the completion row is written AFTER the executor reaches a
        terminal outcome (SUCCESS / FAILED). Writing post-execution
        means a worker that crashes mid-execute leaves no row behind,
        so the JetStream redelivery re-runs the claim instead of being
        silently ack-and-skipped under a stale "we already saw this"
        marker.
        """
        claim_and_raw = await self._task_queue.next_claim(
            timeout=_MAX_FETCH_POLL_SECONDS,
        )
        if claim_and_raw is None:
            return
        claim, raw = claim_and_raw
        if await self._is_completed(claim):
            await self._finalize_claim(raw, TaskClaimStatus.SUCCESS)
            return
        status = await self._execute_claim(claim, raw)
        # Mark before ack: a crash between ``_mark_completed`` and
        # ``_finalize_claim`` still leaves the row in place, so the
        # JetStream redelivery (triggered by the missing ack) observes
        # the completion and ack-skips. The opposite ordering would
        # let a successful claim be silently re-executed when the ack
        # raced redelivery.
        if status in {TaskClaimStatus.SUCCESS, TaskClaimStatus.FAILED}:
            await self._mark_completed(claim)
            await self._finalize_claim(raw, status)
            self._claims_done += 1
            return
        # status == RETRY. WorkQueuePolicy does NOT route to the dead
        # subject on max_deliver -- it terminates the message, silently
        # losing the task. On the final delivery the worker republishes
        # the claim to the dead-letter subject (the DeadLetterConsumer
        # then transitions the task to FAILED) instead of nacking into
        # a silent drop.
        if self._is_final_delivery(raw):
            await self._dead_letter(claim, raw)
            self._claims_done += 1
            return
        await self._finalize_claim(raw, status)

    def _is_final_delivery(self, raw: Msg) -> bool:
        """Return ``True`` when this is the last allowed delivery.

        ``raw.metadata.num_delivered`` is the 1-based delivery count
        JetStream stamps on each message. On the ``max_deliver``-th
        delivery a further nack would exhaust the budget and terminate
        the message with no dead-letter routing, so the worker must
        dead-letter here instead.
        """
        metadata = getattr(raw, "metadata", None)
        num_delivered = getattr(metadata, "num_delivered", 0)
        return int(num_delivered) >= self._queue_config.max_deliver

    async def _dead_letter(self, claim: TaskClaim, raw: Msg) -> None:
        """Republish an exhausted claim to the DLQ, then terminal-ack.

        Publish-then-ack ordering: if ``publish_dead`` fails the claim
        is left un-acked and the failure is fatal (re-raised), so the
        operator sees a loud error rather than a silently lost task.
        Acking only after a successful republish guarantees the
        DeadLetterConsumer will observe the claim and fail the task.
        """
        try:
            await self._task_queue.publish_dead(claim)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                WORKERS_DEAD_LETTER_PUBLISH_FAILED,
                exc,
                worker_id=self._worker_id,
                task_id=claim.task_id,
            )
            raise
        await self._finalize_claim(raw, TaskClaimStatus.SUCCESS)
        logger.warning(
            WORKERS_CLAIM_DEAD_LETTERED,
            worker_id=self._worker_id,
            task_id=claim.task_id,
            idempotency_key=claim.idempotency_key,
        )

    async def _is_completed(self, claim: TaskClaim) -> bool:
        """Return ``True`` if the claim has already completed.

        Resolves to ``False`` when no dedup repository is wired or when
        the repo lookup fails (fail-open: a transient persistence error
        must not stall the worker; the JetStream ``ack_wait`` window
        will redeliver and the next worker tries again).
        """
        if self._seen_claims is None:
            return False
        try:
            seen = await self._seen_claims.is_completed(
                idempotency_key=NotBlankStr(claim.idempotency_key),
            )
        except QueryError as exc:
            logger.warning(
                WORKERS_DEDUP_LOOKUP_FAILED,
                worker_id=self._worker_id,
                task_id=claim.task_id,
                idempotency_key=claim.idempotency_key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if not seen:
            return False
        logger.info(
            WORKERS_DUPLICATE_CLAIM_SUPPRESSED,
            worker_id=self._worker_id,
            task_id=claim.task_id,
            idempotency_key=claim.idempotency_key,
        )
        return True

    async def _mark_completed(self, claim: TaskClaim) -> None:
        """Record the claim's terminal outcome in the dedup repository.

        Fail-open: a transient persistence error here is logged and
        swallowed. The worst-case is that a duplicate redelivery
        executes the work twice; the work itself is idempotent at the
        task-engine layer (single-writer transitions plus the API's
        idempotency-key envelope), so re-execution converges on the
        same state rather than corrupting it. Raising would crash the
        worker loop and stall every claim sharing this subscriber.
        """
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
                WORKERS_DEDUP_MARK_FAILED,
                worker_id=self._worker_id,
                task_id=claim.task_id,
                idempotency_key=claim.idempotency_key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _execute_claim(
        self,
        claim: TaskClaim,
        raw: Msg,
    ) -> TaskClaimStatus:
        """Invoke the executor with a concurrent ack-extension loop.

        Real agent execution can outrun ``ack_wait``. A sibling
        :meth:`_extend_ack_loop` working-acks the JetStream message
        every ``heartbeat_interval_seconds`` so the deadline never
        lapses mid-execution (which would redeliver the claim and run
        the agent a second time concurrently, since dedup only marks
        AFTER a terminal outcome). The extender is cancelled the moment
        the executor returns.

        Returns:
            The terminal claim status returned by the executor.
        """
        logger.info(
            WORKERS_CLAIM_RECEIVED,
            worker_id=self._worker_id,
            task_id=claim.task_id,
        )
        extender = asyncio.create_task(self._extend_ack_loop(raw))
        try:
            return await self._invoke_executor(claim)
        finally:
            extender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await extender

    async def _invoke_executor(self, claim: TaskClaim) -> TaskClaimStatus:
        """Run the injected executor, translating exceptions into RETRY.

        Returns:
            The executor's claim status, or ``RETRY`` when it raises a
            non-critical exception.
        """
        try:
            return await self._executor(claim)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                WORKERS_EXECUTOR_FAILED,
                exc,
                worker_id=self._worker_id,
                task_id=claim.task_id,
            )
            return TaskClaimStatus.RETRY

    async def _extend_ack_loop(self, raw: Msg) -> None:
        """Working-ack *raw* every ack-extend interval until cancelled.

        Sleep-first: the first extension lands one interval in. The
        interval is ``min(heartbeat_interval, ack_wait * 0.5)``, so it
        always fires with at least a full deadline of headroom even if
        the operator tunes ``heartbeat_interval_seconds`` just below
        ``ack_wait_seconds``. An ``in_progress`` failure is non-fatal:
        the executor outcome still drives finalize, and a single missed
        extension is recovered by the next one or by JetStream
        redelivery if the worker dies.
        """
        # lint-allow: long-running-loop-kill-switch -- cancelled by
        # _execute_claim's finally the moment the executor returns.
        while True:
            await self._clock.sleep(self._ack_extend_interval)
            try:
                await JetStreamTaskQueue.in_progress(raw)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    WORKERS_ACK_EXTEND_FAILED,
                    worker_id=self._worker_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    async def _heartbeat_loop(self) -> None:
        """Emit a liveness beat every heartbeat interval until cancelled.

        Runs for the worker's whole lifetime (not per-claim) so an idle
        worker still proves liveness. Cancelled from :meth:`run`'s
        ``finally``. At-most-once core-NATS publish: a missed beat is
        recovered by the next one and never affects correctness.
        """
        # lint-allow: long-running-loop-kill-switch -- cancelled by
        # run()'s finally; the claim loop's _stop_event drives shutdown.
        while True:
            await self._emit_heartbeat()
            await self._clock.sleep(self._heartbeat_interval)

    async def _emit_heartbeat(self) -> None:
        """Publish one :class:`WorkerHeartbeat`; failures are non-fatal."""
        subject = f"{HEARTBEAT_SUBJECT_PREFIX}.{self._worker_id}"
        beat = WorkerHeartbeat(
            worker_id=NotBlankStr(self._worker_id),
            emitted_at=self._clock.now(),
            claims_done=self._claims_done,
        )
        try:
            await self._task_queue.core_publish(
                subject,
                beat.model_dump_json().encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                WORKERS_HEARTBEAT_FAILED,
                worker_id=self._worker_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.debug(
            WORKERS_HEARTBEAT_SENT,
            worker_id=self._worker_id,
            claims_done=self._claims_done,
        )

    async def _finalize_claim(
        self,
        raw: Msg,
        status: TaskClaimStatus,
    ) -> None:
        """Ack or nack the JetStream message based on outcome.

        A finalize failure is fatal: the executor may already have
        transitioned the task via HTTP, so swallowing the exception
        would let JetStream redeliver the same claim and cause the
        executor to run a second time. Log with context and re-raise
        so the outer ``run()`` loop exits and the worker process is
        restarted by the pool (or by an orchestrator).
        """
        terminal = {TaskClaimStatus.SUCCESS, TaskClaimStatus.FAILED}
        try:
            if status in terminal:
                await JetStreamTaskQueue.ack(raw)
            else:
                await JetStreamTaskQueue.nack(raw)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                WORKERS_FINALIZE_FAILED,
                exc,
                worker_id=self._worker_id,
                status=str(status),
            )
            raise


async def run_worker_pool(  # noqa: PLR0913 -- canonical worker-pool entry point
    *,
    queue_config: QueueConfig,
    task_queue: JetStreamTaskQueue,
    executor: TaskExecutor,
    worker_count: int,
    worker_id_prefix: str = "worker",
    seen_claims: SeenClaimsRepository | None = None,
    clock: Clock | None = None,
) -> None:
    """Run ``worker_count`` workers concurrently until cancelled.

    Blocks until all workers exit (via ``stop`` or cancellation).
    Uses :class:`asyncio.TaskGroup` so a failing worker propagates
    the exception after sibling cancellation.

    Args:
        queue_config: Queue configuration forwarded to each
            :class:`Worker` (ack wait, max deliver).
        task_queue: Connected :class:`JetStreamTaskQueue` shared
            across all workers in the pool.
        executor: Async callable invoked for each fetched claim.
        worker_count: Number of concurrent workers to spawn.
        worker_id_prefix: Prefix used to compose each worker's
            identifier (e.g. ``"worker-0"``).
        seen_claims: Optional dedup repository forwarded to every
            spawned :class:`Worker`. When ``None``, claim dedup is
            disabled; production callers must wire this from the
            persistence backend.
        clock: Optional clock seam forwarded to every spawned worker.
    """
    workers = [
        Worker(
            queue_config=queue_config,
            task_queue=task_queue,
            executor=executor,
            worker_id=f"{worker_id_prefix}-{i}",
            seen_claims=seen_claims,
            clock=clock,
        )
        for i in range(worker_count)
    ]
    logger.info(
        WORKERS_POOL_STARTED,
        worker_count=worker_count,
    )
    try:
        async with asyncio.TaskGroup() as tg:
            for worker in workers:
                _ = tg.create_task(worker.run())
    finally:
        # Best-effort drain; surface stop failures instead of swallowing them.
        # return_exceptions keeps one slow stop from stranding the rest, and a
        # cancelled gather still propagates (CancelledError is not Exception).
        results = await asyncio.gather(
            *(w.stop() for w in workers), return_exceptions=True
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        for failure in failures:
            reraise_critical(failure)
        if failures:
            logger.warning(
                WORKERS_POOL_STOP_FAILED,
                failed_count=len(failures),
                error_types=sorted({type(f).__name__ for f in failures}),
            )
