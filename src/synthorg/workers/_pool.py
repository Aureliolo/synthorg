# module-kind: code
"""Concurrent worker-pool runner for the distributed task path.

``run_worker_pool`` spawns a fixed number of :class:`Worker` instances
against one shared :class:`JetStreamTaskQueue` and blocks until they all
exit. Kept beside :mod:`synthorg.workers.worker` so the worker module
stays focused on the single-worker fetch/execute/finalise loop.
"""

import asyncio

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.workers import (
    WORKERS_POOL_STARTED,
    WORKERS_POOL_STOP_FAILED,
)
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository
from synthorg.workers.claim import JetStreamTaskQueue
from synthorg.workers.config import QueueConfig
from synthorg.workers.worker import TaskExecutor, Worker

logger = get_logger(__name__)


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
