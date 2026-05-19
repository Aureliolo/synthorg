"""Unit tests for the backend-side dead-letter consumer.

Asserts the no-loss closure: a claim that exhausted ``max_deliver`` is
driven to FAILED exactly once, idempotently, and a case that cannot be
proven failed raises loudly rather than acking into silent loss.
"""

import asyncio
from typing import TYPE_CHECKING, Final

import pytest

from synthorg.core.workers_errors import WorkerDeadLetterError
from synthorg.workers.claim import TaskClaim
from synthorg.workers.config import QueueConfig
from synthorg.workers.dead_letter import (
    DeadLetterConsumer,
    DeadLetterOutcome,
    TaskFailer,
)
from tests._shared.fake_clock import FakeClock
from tests._shared.fake_task_queue import FakeJetStreamTaskQueue
from tests._shared.persistence import make_sqlite_seen_claims

if TYPE_CHECKING:
    from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository

pytestmark = pytest.mark.unit

_HARD_CAP_SECONDS: Final[float] = 5.0
_POLL_SECONDS: Final[float] = 0.01


def _claim(task_id: str = "task-x") -> TaskClaim:
    return TaskClaim(task_id=task_id, new_status="assigned")


def _failer(outcome: DeadLetterOutcome, *, calls: list[str]) -> TaskFailer:
    async def _fail(task_id: str, reason: str) -> DeadLetterOutcome:
        calls.append(task_id)
        return outcome

    return _fail


async def _next_dead(
    queue: FakeJetStreamTaskQueue,
) -> tuple[TaskClaim, object]:
    pair = await queue.next_dead(timeout=_HARD_CAP_SECONDS)
    assert pair is not None
    return pair


def _consumer(
    queue: FakeJetStreamTaskQueue,
    failer: TaskFailer,
    *,
    seen: SeenClaimsRepository | None = None,
    max_deliver: int = 3,
) -> DeadLetterConsumer:
    return DeadLetterConsumer(
        task_queue=queue,  # type: ignore[arg-type]
        task_failer=failer,
        queue_config=QueueConfig(enabled=True, max_deliver=max_deliver),
        seen_claims=seen,
        clock=FakeClock(),
    )


async def test_transitioned_marks_and_acks() -> None:
    queue = FakeJetStreamTaskQueue()
    calls: list[str] = []
    consumer = _consumer(queue, _failer(DeadLetterOutcome.TRANSITIONED, calls=calls))
    queue.deliver_dead(_claim("task-1"))
    claim, raw = await _next_dead(queue)

    await consumer._handle(claim, raw)

    assert calls == ["task-1"]
    assert queue.dead_acked == [claim]


async def test_already_terminal_acks_without_error() -> None:
    queue = FakeJetStreamTaskQueue()
    calls: list[str] = []
    consumer = _consumer(
        queue, _failer(DeadLetterOutcome.ALREADY_TERMINAL, calls=calls)
    )
    queue.deliver_dead(_claim("task-2"))
    claim, raw = await _next_dead(queue)

    await consumer._handle(claim, raw)

    assert queue.dead_acked == [claim]


async def test_not_found_acks() -> None:
    queue = FakeJetStreamTaskQueue()
    consumer = _consumer(queue, _failer(DeadLetterOutcome.NOT_FOUND, calls=[]))
    queue.deliver_dead(_claim("gone"))
    claim, raw = await _next_dead(queue)

    await consumer._handle(claim, raw)

    assert queue.dead_acked == [claim]


async def test_retryable_not_exhausted_nacks() -> None:
    queue = FakeJetStreamTaskQueue(max_deliver=3)
    consumer = _consumer(
        queue, _failer(DeadLetterOutcome.RETRYABLE, calls=[]), max_deliver=3
    )
    queue.deliver_dead(_claim("retry"))
    claim, raw = await _next_dead(queue)

    await consumer._handle(claim, raw)

    assert raw.nak_count == 1  # type: ignore[attr-defined]
    assert queue.dead_acked == []


async def test_retryable_exhausted_raises() -> None:
    queue = FakeJetStreamTaskQueue(max_deliver=1)
    consumer = _consumer(
        queue, _failer(DeadLetterOutcome.RETRYABLE, calls=[]), max_deliver=1
    )
    queue.deliver_dead(_claim("stuck"))
    claim, raw = await _next_dead(queue)

    with pytest.raises(WorkerDeadLetterError):
        await consumer._handle(claim, raw)


async def test_unmapped_failer_exception_raises_loud() -> None:
    queue = FakeJetStreamTaskQueue()

    async def _boom(task_id: str, reason: str) -> DeadLetterOutcome:
        msg = "engine seam blew up"
        raise RuntimeError(msg)

    consumer = _consumer(queue, _boom)
    queue.deliver_dead(_claim("boom"))
    claim, raw = await _next_dead(queue)

    with pytest.raises(WorkerDeadLetterError):
        await consumer._handle(claim, raw)


async def test_duplicate_dead_claim_suppressed_via_real_repo() -> None:
    """A redelivered dead claim ack-skips, never failing the task twice."""
    queue = FakeJetStreamTaskQueue()
    calls: list[str] = []
    async with make_sqlite_seen_claims() as repo:
        consumer = _consumer(
            queue,
            _failer(DeadLetterOutcome.TRANSITIONED, calls=calls),
            seen=repo,
        )
        claim = _claim("task-dup")
        queue.deliver_dead(claim)
        first_claim, first_raw = await _next_dead(queue)
        await consumer._handle(first_claim, first_raw)
        # Redelivery of the same dead message (lost ack).
        queue.deliver_dead(claim)
        second_claim, second_raw = await _next_dead(queue)
        await consumer._handle(second_claim, second_raw)

    assert calls == ["task-dup"], "task failed more than once"
    assert len(queue.dead_acked) == 2, "redelivered dead claim not acked"


async def test_start_stop_lifecycle_processes_via_loop() -> None:
    """The background loop drains the dead subject and is restart-safe."""
    queue = FakeJetStreamTaskQueue()
    calls: list[str] = []
    consumer = _consumer(queue, _failer(DeadLetterOutcome.TRANSITIONED, calls=calls))
    await consumer.start()
    with pytest.raises(RuntimeError, match="already running"):
        await consumer.start()
    queue.deliver_dead(_claim("looped"))

    # Bounded poll: asyncio.timeout is the hard cap; no Event to await.
    async with asyncio.timeout(_HARD_CAP_SECONDS):
        while not calls:  # noqa: ASYNC110
            await asyncio.sleep(_POLL_SECONDS)
    await consumer.stop()
    await consumer.stop()  # idempotent

    assert calls == ["looped"]
    assert consumer.is_running is False
