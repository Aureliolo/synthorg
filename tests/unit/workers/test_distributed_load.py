"""Synthetic-load invariants for the distributed worker path.

Fast deterministic layer (no broker) asserting the no-loss /
no-duplication contract plus each hardening behaviour. The
acceptance-grade real-NATS proof lives in
``tests/integration/workers/test_distributed_path_nats.py``; these
tests assert logic invariants the fake queue can model exactly.
"""

import asyncio
from collections.abc import Callable, Iterator
from typing import Final

import pytest
from typeguard import suppress_type_checks

from synthorg.workers.claim import TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.worker import Worker
from tests._shared.fake_clock import FakeClock
from tests._shared.fake_task_queue import FakeJetStreamTaskQueue
from tests._shared.persistence import make_sqlite_seen_claims

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_task_queue_doubles() -> Iterator[None]:
    """Suppress typeguard module-wide for the synthetic-load worker tests.

    The no-loss / no-duplication invariants are asserted against a
    ``FakeJetStreamTaskQueue`` that models the broker exactly without a real
    NATS binding; the tests verify those invariants, not task-queue type
    conformance. ``JetStreamTaskQueue`` is a concrete class whose ``isinstance``
    check the fake cannot satisfy, so the runtime check is suppressed for the
    module.
    """
    with suppress_type_checks():
        yield


_HARD_CAP_SECONDS: Final[float] = 5.0
"""Wall-clock ceiling for every bounded wait in this module.

A genuine no-loss regression manifests as a claim that never reaches a
terminal state; the cap converts that hang into a fast, legible
failure instead of a suite timeout. Never raise this to paper over a
slow path."""

_POLL_SECONDS: Final[float] = 0.01
"""Re-check interval while waiting on an invariant to settle."""


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Poll *predicate* until true or the hard cap elapses."""
    # Bounded poll on external state (queue counters): there is no Event
    # to await; asyncio.timeout above is the load-bearing hard cap.
    async with asyncio.timeout(_HARD_CAP_SECONDS):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(_POLL_SECONDS)


def _claim(task_id: str) -> TaskClaim:
    return TaskClaim(task_id=task_id, new_status="assigned")


async def _run_workers_until(
    *,
    workers: list[Worker],
    predicate: Callable[[], bool],
) -> None:
    """Run *workers* concurrently, stop them once *predicate* holds."""
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(w.run()) for w in workers]
        await _wait_until(predicate)
        for w in workers:
            await w.stop()
        for t in tasks:
            await t


async def test_no_loss_no_duplication_under_concurrent_workers() -> None:
    """N workers over M distinct claims: each executed exactly once."""
    worker_count: Final[int] = 4
    claim_count: Final[int] = 25
    queue = FakeJetStreamTaskQueue()
    seen = []
    lock = asyncio.Lock()

    async def executor(claim: TaskClaim) -> TaskClaimStatus:
        async with lock:
            seen.append(claim.task_id)
        return TaskClaimStatus.SUCCESS

    config = QueueConfig(enabled=True)
    async with make_sqlite_seen_claims() as repo:
        workers = [
            Worker(
                queue_config=config,
                task_queue=queue,  # type: ignore[arg-type]
                executor=executor,
                worker_id=f"w-{i}",
                seen_claims=repo,
            )
            for i in range(worker_count)
        ]
        for i in range(claim_count):
            await queue.publish_claim(_claim(f"task-{i}"))
        await _run_workers_until(
            workers=workers,
            predicate=lambda: len(queue.acked) >= claim_count,
        )

    assert sorted(seen) == sorted(f"task-{i}" for i in range(claim_count))
    assert len(seen) == claim_count, "duplicate execution detected"
    assert len(queue.acked) == claim_count, "claim lost (never acked)"
    assert queue.terminated == [], "claim silently terminated"


async def test_redelivery_after_completion_is_deduped() -> None:
    """A redelivered, already-completed claim ack-skips, never re-runs.

    Uses the real SQLite ``SeenClaimsRepository`` so the dedup is
    exercised against production SQL, not a stub.
    """
    queue = FakeJetStreamTaskQueue()
    invocations: list[str] = []

    async def executor(claim: TaskClaim) -> TaskClaimStatus:
        invocations.append(claim.task_id)
        return TaskClaimStatus.SUCCESS

    config = QueueConfig(enabled=True)
    claim = _claim("task-dup")
    async with make_sqlite_seen_claims() as repo:
        worker = Worker(
            queue_config=config,
            task_queue=queue,  # type: ignore[arg-type]
            executor=executor,
            worker_id="w-0",
            seen_claims=repo,
        )
        await queue.publish_claim(claim)
        await _run_workers_until(
            workers=[worker],
            predicate=lambda: len(queue.acked) >= 1,
        )
        # Same idempotency key redelivered (lost ack / crash-before-ack).
        await queue.publish_claim(claim)
        worker2 = Worker(
            queue_config=config,
            task_queue=queue,  # type: ignore[arg-type]
            executor=executor,
            worker_id="w-1",
            seen_claims=repo,
        )
        await _run_workers_until(
            workers=[worker2],
            predicate=lambda: len(queue.acked) >= 2,
        )

    assert invocations == ["task-dup"], "duplicate execution on redelivery"
    assert len(queue.acked) == 2, "redelivered claim was not acked"


async def test_worker_extends_ack_during_long_execution() -> None:
    """While the executor runs, the worker working-acks the message.

    Real agent execution exceeds ``ack_wait``; without periodic
    ``in_progress()`` JetStream redelivers mid-execution and the task
    runs twice. Asserts the extender ticks at least twice before the
    executor returns, then the claim is acked exactly once.
    """
    queue = FakeJetStreamTaskQueue()
    release = asyncio.Event()

    async def slow_executor(_claim_arg: TaskClaim) -> TaskClaimStatus:
        await release.wait()
        return TaskClaimStatus.SUCCESS

    config = QueueConfig(enabled=True, heartbeat_interval_seconds=1)
    worker = Worker(
        queue_config=config,
        task_queue=queue,  # type: ignore[arg-type]
        executor=slow_executor,
        worker_id="w-0",
        clock=FakeClock(),
    )
    await queue.publish_claim(_claim("task-slow"))
    async with asyncio.TaskGroup() as tg:
        run_task = tg.create_task(worker.run())
        await _wait_until(lambda: queue.in_progress_total >= 2)
        release.set()
        await _wait_until(lambda: len(queue.acked) == 1)
        await worker.stop()
        await run_task

    assert queue.acked[0].task_id == "task-slow"
    assert queue.terminated == []


async def test_max_deliver_exceeded_routes_to_dead_subject() -> None:
    """A claim that exhausts ``max_deliver`` is republished to DLQ.

    Without the worker's dead-letter republish, JetStream terminates
    the message and the task is silently lost (never FAILED). The
    queue double captures that loss in ``terminated``; the invariant
    is that the claim instead lands in ``dead_letters``.
    """
    max_deliver: Final[int] = 2
    queue = FakeJetStreamTaskQueue(max_deliver=max_deliver)

    async def always_retry(_claim_arg: TaskClaim) -> TaskClaimStatus:
        return TaskClaimStatus.RETRY

    config = QueueConfig(enabled=True, max_deliver=max_deliver)
    worker = Worker(
        queue_config=config,
        task_queue=queue,  # type: ignore[arg-type]
        executor=always_retry,
        worker_id="w-0",
    )
    await queue.publish_claim(_claim("task-dead"))
    await _run_workers_until(
        workers=[worker],
        predicate=lambda: len(queue.dead_letters) >= 1,
    )

    assert [c.task_id for c in queue.dead_letters] == ["task-dead"]


async def test_worker_emits_heartbeat_on_interval() -> None:
    """The worker publishes a liveness heartbeat on a core-NATS subject."""
    queue = FakeJetStreamTaskQueue()
    release = asyncio.Event()

    async def slow_executor(_claim_arg: TaskClaim) -> TaskClaimStatus:
        await release.wait()
        return TaskClaimStatus.SUCCESS

    config = QueueConfig(enabled=True, heartbeat_interval_seconds=1)
    worker = Worker(
        queue_config=config,
        task_queue=queue,  # type: ignore[arg-type]
        executor=slow_executor,
        worker_id="hb-worker",
        clock=FakeClock(),
    )
    await queue.publish_claim(_claim("task-hb"))
    async with asyncio.TaskGroup() as tg:
        run_task = tg.create_task(worker.run())
        await _wait_until(lambda: len(queue.core_published) >= 2)
        release.set()
        await _wait_until(lambda: len(queue.acked) == 1)
        await worker.stop()
        await run_task

    subjects = {subject for subject, _ in queue.core_published}
    assert any(s.endswith("hb-worker") for s in subjects), subjects


def test_backpressure_config_fields_present() -> None:
    """``QueueConfig`` exposes the backpressure levers with sane bounds."""
    config = QueueConfig(enabled=True)
    assert config.max_ack_pending > 0
    assert config.stream_max_msgs > 0
    assert config.stream_max_bytes > 0
    assert config.prune_interval_seconds > 0
