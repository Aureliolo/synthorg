"""Acceptance-grade synthetic-load tests for the distributed path.

Runs the real worker pool + dead-letter consumer against a real NATS
JetStream container (testcontainers), reusing the pattern from
``tests/integration/communication/test_bus_nats.py``. These tests
exercise the JetStream timing/state behaviours a fake queue cannot
model: real ``ack_wait`` redelivery under long execution, real
``max_deliver`` termination, and real multi-consumer work-queue
distribution. This is the proof for the distributed-path acceptance
criterion ("multi-worker run under synthetic load, NO loss or duplication").

Short ``QueueConfig`` timings keep each test to a few seconds; the
shipped defaults (300 / 3 / 30) would make these hang.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Final

import pytest

from synthorg.communication.config import NatsConfig
from synthorg.workers.claim import JetStreamTaskQueue, TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.dead_letter import DeadLetterConsumer, DeadLetterOutcome
from synthorg.workers.worker import run_worker_pool
from tests._shared.persistence import make_sqlite_seen_claims

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]
"""Override the 30s global pytest-timeout to give the NATS JetStream
testcontainer room to start on cold runners. Mirrors the established
pattern used by other testcontainer-backed integration tests (Postgres
timescaledb / jsonb-benchmark, Docker sandbox isolation, browser tests),
all of which run on a 120s budget. Without this override, a cold-cache
``DockerContainer`` pull + start eats most of the 30s budget before the
first claim even publishes."""

_HARD_CAP_SECONDS: Final[float] = 25.0
"""Wall-clock ceiling per bounded wait. A no-loss regression manifests
as a claim that never reaches a terminal state; the cap turns that
hang into a fast, legible failure rather than a suite timeout. Held
below the per-module 120s test timeout so a tripped cap fails with a
clear assertion instead of racing the suite-level timeout."""

_ACK_WAIT_SECONDS: Final[int] = 2
_MAX_DELIVER: Final[int] = 2
_HEARTBEAT_SECONDS: Final[int] = 1


@pytest.fixture(scope="module")
def nats_url() -> Iterator[str]:
    """Start a NATS JetStream container for the module's tests."""
    try:
        from testcontainers.core.container import DockerContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    # renovate: datasource=docker depName=nats
    container = DockerContainer("nats:2.12.6-alpine")
    container.with_command("-js")
    container.with_exposed_ports(4222)
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"could not start NATS container: {exc}")

    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(4222))
    try:
        yield f"nats://{host}:{port}"
    finally:
        container.stop()


def _queue_config(**overrides: object) -> QueueConfig:
    base: dict[str, object] = {
        "enabled": True,
        "ack_wait_seconds": _ACK_WAIT_SECONDS,
        "max_deliver": _MAX_DELIVER,
        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
    }
    base.update(overrides)
    return QueueConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
async def task_queue(nats_url: str) -> AsyncIterator[JetStreamTaskQueue]:
    """A started queue on per-test unique stream + subjects.

    JetStream rejects a second stream whose subjects overlap an
    existing one (err 10065). A unique ``stream_name`` alone is not
    enough because the subject prefixes default to a shared constant
    and persist server-side after the fixture only drains the client.
    Deriving the subject prefixes from the same suffix keeps each
    test's stream fully isolated and the module parallel-safe.
    """
    suffix = uuid.uuid4().hex[:8].upper()
    queue = JetStreamTaskQueue(
        queue_config=_queue_config(
            stream_name=f"SYNTHORG_TASKS_{suffix}",
            ready_subject_prefix=f"synthorg.tasks.{suffix}.ready",
            dead_subject_prefix=f"synthorg.tasks.{suffix}.dead",
        ),
        nats_config=NatsConfig(url=nats_url, connect_timeout_seconds=10.0),
        durable_name=f"workers_{suffix}",
    )
    await _bounded_setup(queue.start())
    try:
        yield queue
    finally:
        if queue.is_running:
            await _bounded_setup(queue.stop())


async def _wait_until(
    changed: asyncio.Condition,
    predicate: Callable[[], bool],
) -> None:
    """Await *predicate* on the *changed* handshake, bounded by the hard cap.

    Each executor / fail-handler notifies *changed* after recording its
    progress, so the wait yields exactly when the awaited state appears rather
    than on a polling interval; ``_HARD_CAP_SECONDS`` is the load-bearing cap.
    """
    async with asyncio.timeout(_HARD_CAP_SECONDS):
        async with changed:
            await changed.wait_for(predicate)


async def _shutdown_pool(pool: asyncio.Task[None]) -> None:
    """Cancel the worker pool and join its exit, bounded by the hard cap.

    The pool's shutdown drains real JetStream subscriptions and joins the
    per-worker run loops. Under heavy parallel-shard contention that join can
    stall; left unbounded it would run to the module-level timeout, which fires
    ``SIGABRT`` and takes the whole xdist worker down (failing the entire shard
    rather than this one test). Bounding the join applies the same
    ``_HARD_CAP_SECONDS`` "hang -> fast, legible failure" contract the
    happy-path wait already uses: a stalled teardown surfaces as a clean
    per-test ``TimeoutError`` instead of a suite-killing abort. The expected
    ``CancelledError`` raised by the cancelled pool is swallowed; a cap breach
    propagates as ``TimeoutError``.
    """
    pool.cancel()
    try:
        async with asyncio.timeout(_HARD_CAP_SECONDS):
            await pool
    except asyncio.CancelledError:
        pass


async def _bounded_setup(step: Awaitable[object]) -> None:
    """Cap a single setup await at ``_HARD_CAP_SECONDS``.

    The happy-path wait and the pool shutdown are already capped, but the
    fixture's ``queue.start()`` / ``stop()`` and the per-test publish /
    consumer steps were not. Under JetStream-container contention one of those
    awaits (a publish awaiting an ack, stream/consumer creation, or a drain on
    stop) can stall indefinitely and run the test to the module-level timeout,
    which fires ``SIGABRT`` and takes the whole xdist worker down (the observed
    "node down" failure). Capping setup gives it the same "hang -> fast, legible
    per-test ``TimeoutError``" contract the wait and shutdown already use.

    Use this for a *single* setup await; a sequence (a publish loop, or
    start-then-publish) is instead wrapped in one ``asyncio.timeout`` block so
    the whole phase shares a single cap, rather than letting per-step caps sum
    past the module timeout.
    """
    async with asyncio.timeout(_HARD_CAP_SECONDS):
        await step


async def test_synthetic_load_no_loss_no_duplication(
    task_queue: JetStreamTaskQueue,
) -> None:
    """K workers over M claims: every task executed exactly once."""
    worker_count: Final[int] = 4
    claim_count: Final[int] = 30
    executed: list[str] = []
    lock = asyncio.Lock()
    changed = asyncio.Condition()

    async def executor(claim: TaskClaim) -> TaskClaimStatus:
        async with lock:
            executed.append(str(claim.task_id))
        async with changed:
            changed.notify_all()
        return TaskClaimStatus.SUCCESS

    async with make_sqlite_seen_claims() as repo:
        # One cap over the whole publish phase: a per-call cap would let the
        # loop's cumulative time exceed the module timeout (and SIGABRT)
        # without any single publish tripping its own cap.
        async with asyncio.timeout(_HARD_CAP_SECONDS):
            for i in range(claim_count):
                await task_queue.publish_claim(
                    TaskClaim(task_id=f"task-{i}", new_status="assigned"),
                )
        pool = asyncio.create_task(
            run_worker_pool(
                queue_config=task_queue._queue_config,
                task_queue=task_queue,
                executor=executor,
                worker_count=worker_count,
                seen_claims=repo,
            ),
        )
        try:
            await _wait_until(changed, lambda: len(executed) >= claim_count)
        finally:
            await _shutdown_pool(pool)

    assert sorted(executed) == sorted(f"task-{i}" for i in range(claim_count)), (
        "loss or duplication detected"
    )


async def test_long_execution_extends_ack_no_duplicate(
    task_queue: JetStreamTaskQueue,
) -> None:
    """Execution longer than ack_wait must not cause a second run.

    Without the worker's periodic ``in_progress`` working-ack, real
    JetStream would redeliver this claim after ``ack_wait`` and a
    second worker would run it concurrently. Exactly-once proves the
    ack-extension holds against a real broker.
    """
    runs: list[str] = []
    duplicate_run = asyncio.Event()
    changed = asyncio.Condition()

    async def slow_executor(claim: TaskClaim) -> TaskClaimStatus:
        task_id = str(claim.task_id)
        runs.append(task_id)
        if runs.count(task_id) > 1:
            duplicate_run.set()
        async with changed:
            changed.notify_all()
        # Deliberate ~2x ack_wait latency (not a poll): without the worker's
        # in_progress ack-extension, real JetStream would redeliver here.
        await asyncio.sleep(_ACK_WAIT_SECONDS * 2 + 1)
        return TaskClaimStatus.SUCCESS

    async with make_sqlite_seen_claims() as repo:
        await _bounded_setup(
            task_queue.publish_claim(
                TaskClaim(task_id="slow-task", new_status="assigned"),
            )
        )
        pool = asyncio.create_task(
            run_worker_pool(
                queue_config=task_queue._queue_config,
                task_queue=task_queue,
                executor=slow_executor,
                worker_count=2,
                seen_claims=repo,
            ),
        )
        try:
            await _wait_until(changed, lambda: runs.count("slow-task") >= 1)
            # A redelivery would set duplicate_run; its absence within
            # multiple ack windows proves the working-ack held without
            # a fixed long sleep.
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(_ACK_WAIT_SECONDS * 3):
                    await duplicate_run.wait()
        finally:
            await _shutdown_pool(pool)

    assert runs.count("slow-task") == 1, f"duplicate execution: {runs}"


async def test_max_deliver_dead_letters_to_failed_no_loss(
    task_queue: JetStreamTaskQueue,
) -> None:
    """A claim that always RETRYs ends FAILED via the DLQ, never lost."""
    failed: list[str] = []
    changed = asyncio.Condition()

    async def fail_handler(task_id: str, reason: str) -> DeadLetterOutcome:
        failed.append(task_id)
        async with changed:
            changed.notify_all()
        return DeadLetterOutcome.TRANSITIONED

    async def always_retry(claim: TaskClaim) -> TaskClaimStatus:
        return TaskClaimStatus.RETRY

    async with make_sqlite_seen_claims() as repo:
        consumer = DeadLetterConsumer(
            task_queue=task_queue,
            task_fail_handler=fail_handler,
            queue_config=task_queue._queue_config,
            seen_claims=repo,
        )
        # Once the consumer exists, guarantee stop() runs even if the bounded
        # setup times out after start() succeeds (stop() no-ops when not
        # running). The start+publish phase shares a single cap so cumulative
        # setup time stays bounded.
        try:
            async with asyncio.timeout(_HARD_CAP_SECONDS):
                await consumer.start()
                await task_queue.publish_claim(
                    TaskClaim(task_id="doomed-task", new_status="assigned"),
                )
            pool = asyncio.create_task(
                run_worker_pool(
                    queue_config=task_queue._queue_config,
                    task_queue=task_queue,
                    executor=always_retry,
                    worker_count=2,
                    seen_claims=repo,
                ),
            )
            try:
                await _wait_until(changed, lambda: failed.count("doomed-task") >= 1)
            finally:
                await _shutdown_pool(pool)
        finally:
            await _bounded_setup(consumer.stop())

    assert failed == ["doomed-task"], f"task lost or double-failed: {failed}"
