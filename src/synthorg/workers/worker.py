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
from typing import Any, Final

from synthorg.observability import get_logger
from synthorg.observability.events.workers import (
    WORKERS_CLAIM_RECEIVED,
    WORKERS_EXECUTOR_FAILED,
    WORKERS_FINALIZE_FAILED,
    WORKERS_POOL_STARTED,
    WORKERS_WORKER_STARTED,
    WORKERS_WORKER_STOPPED,
)
from synthorg.workers.claim import (
    JetStreamTaskQueue,
    TaskClaim,
    TaskClaimStatus,
)
from synthorg.workers.config import QueueConfig  # noqa: TC001

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


class Worker:
    """Single-process distributed worker.

    Args:
        queue_config: Queue configuration (ack wait, max deliver).
        task_queue: Connected :class:`JetStreamTaskQueue`.
        executor: Async callable invoked for each claim.
        worker_id: Identifier for logging + heartbeat subject.
    """

    def __init__(
        self,
        *,
        queue_config: QueueConfig,
        task_queue: JetStreamTaskQueue,
        executor: TaskExecutor,
        worker_id: str,
    ) -> None:
        self._queue_config = queue_config
        self._task_queue = task_queue
        self._executor = executor
        self._worker_id = worker_id
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
        """
        async with self._lifecycle_lock:
            if self._running:
                msg = f"Worker {self._worker_id} is already running"
                raise RuntimeError(msg)
            self._running = True
            self._stop_event.clear()
            logger.info(WORKERS_WORKER_STARTED, worker_id=self._worker_id)

        try:
            # lint-allow: long-running-loop-kill-switch -- _stop_event drives shutdown.
            while not self._stop_event.is_set():
                await self._run_once()
        finally:
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
        """
        claim_and_raw = await self._task_queue.next_claim(
            timeout=_MAX_FETCH_POLL_SECONDS,
        )
        if claim_and_raw is None:
            return
        claim, raw = claim_and_raw
        status = await self._execute_claim(claim)
        await self._finalize_claim(raw, status)

    async def _execute_claim(self, claim: TaskClaim) -> TaskClaimStatus:
        """Invoke the executor, translating exceptions into RETRY."""
        logger.info(
            WORKERS_CLAIM_RECEIVED,
            worker_id=self._worker_id,
            task_id=claim.task_id,
        )
        try:
            return await self._executor(claim)
        except Exception:
            logger.exception(
                WORKERS_EXECUTOR_FAILED,
                worker_id=self._worker_id,
                task_id=claim.task_id,
            )
            return TaskClaimStatus.RETRY

    async def _finalize_claim(
        self,
        raw: Any,
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
        except Exception:
            logger.exception(
                WORKERS_FINALIZE_FAILED,
                worker_id=self._worker_id,
                status=str(status),
            )
            raise


async def run_worker_pool(
    *,
    queue_config: QueueConfig,
    task_queue: JetStreamTaskQueue,
    executor: TaskExecutor,
    worker_count: int,
    worker_id_prefix: str = "worker",
) -> None:
    """Run ``worker_count`` workers concurrently until cancelled.

    Blocks until all workers exit (via ``stop`` or cancellation).
    Uses :class:`asyncio.TaskGroup` so a failing worker propagates
    the exception after sibling cancellation.
    """
    workers = [
        Worker(
            queue_config=queue_config,
            task_queue=task_queue,
            executor=executor,
            worker_id=f"{worker_id_prefix}-{i}",
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
                tg.create_task(worker.run())
    finally:
        with contextlib.suppress(Exception):
            await asyncio.gather(
                *(w.stop() for w in workers),
                return_exceptions=True,
            )
