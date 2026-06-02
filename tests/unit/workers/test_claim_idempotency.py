# mypy: disable-error-code="explicit-any"
"""Unit coverage for TaskClaim idempotency_key + worker dedup.

The worker-side dedup tests use a stub ``SeenClaimsRepository`` that
records every ``is_completed`` / ``mark_seen`` call so the contract
can be asserted without spinning up a real persistence backend. Each
flow test drives the full ``Worker._run_once`` path through a stub
``JetStreamTaskQueue`` so the regression coverage exercises the
ordering invariants (pre-execute is_completed read, post-terminal
mark_seen, RETRY never marks) that the dedup design depends on.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from typeguard import suppress_type_checks

from synthorg.core.types import NotBlankStr
from synthorg.workers.claim import TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.worker import Worker
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_task_queue_doubles() -> Iterator[None]:
    """Suppress typeguard module-wide for the worker dedup / idempotency tests.

    Each flow drives ``Worker._run_once`` through a stub ``JetStreamTaskQueue``
    (``_NullTaskQueue`` / ``_ScriptedTaskQueue``) whose claim iteration is
    scripted; they verify dedup ordering invariants, not task-queue type
    conformance. ``JetStreamTaskQueue`` is a concrete class whose ``isinstance``
    check a behavioural stub cannot satisfy without a live JetStream binding,
    so the runtime check is suppressed for the module.
    """
    with suppress_type_checks():
        yield


class _StubSeenClaims:
    """Records every is_completed/mark_seen invocation in order."""

    def __init__(
        self,
        *,
        completed_keys: frozenset[str] | None = None,
    ) -> None:
        self.is_completed_calls: list[str] = []
        self.mark_seen_calls: list[dict[str, Any]] = []
        self._completed_keys: set[str] = set(completed_keys or frozenset())

    async def is_completed(
        self,
        *,
        idempotency_key: NotBlankStr,
    ) -> bool:
        self.is_completed_calls.append(str(idempotency_key))
        return str(idempotency_key) in self._completed_keys

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        self.mark_seen_calls.append(
            {
                "idempotency_key": str(idempotency_key),
                "claim_id": str(claim_id),
                "now": now,
                "ttl_seconds": ttl_seconds,
            },
        )
        first = str(idempotency_key) not in self._completed_keys
        self._completed_keys.add(str(idempotency_key))
        return first

    async def prune_expired(self, now: datetime) -> int:
        del now
        return 0


class _StubRawMessage:
    """Stand-in for the NATS JetStream message handle the worker acks.

    ``JetStreamTaskQueue.ack`` / ``nack`` reach into ``raw.ack()`` /
    ``raw.nak()`` directly so the worker test needs a minimal handle
    that records each call without pulling in a live NATS client.
    """

    def __init__(self) -> None:
        self.ack_calls: int = 0
        self.nak_calls: list[float] = []

    async def ack(self) -> None:
        self.ack_calls += 1

    async def nak(self, delay: float = 0.0) -> None:
        self.nak_calls.append(delay)


class _ScriptedTaskQueue:
    """Yields the queued (claim, raw) pairs in order, then None."""

    is_running = True

    def __init__(
        self,
        deliveries: list[tuple[TaskClaim, _StubRawMessage]],
    ) -> None:
        self._deliveries = list(deliveries)

    async def next_claim(
        self,
        timeout: float,  # noqa: ASYNC109 -- mirrors prod signature
    ) -> tuple[TaskClaim, _StubRawMessage] | None:
        del timeout
        if not self._deliveries:
            return None
        return self._deliveries.pop(0)


class _NullTaskQueue:
    """Stand-in for ``JetStreamTaskQueue`` that never yields a claim."""

    is_running = True

    async def next_claim(self, timeout: float) -> Any:  # noqa: ASYNC109 -- mirrors prod signature; pragma: no cover
        del timeout
        return None


class TestTaskClaimIdempotencyKey:
    def test_idempotency_key_defaults_to_fresh_uuid(self) -> None:
        first = TaskClaim(
            task_id=NotBlankStr("t-1"), new_status=NotBlankStr("assigned")
        )
        second = TaskClaim(
            task_id=NotBlankStr("t-1"), new_status=NotBlankStr("assigned")
        )
        assert first.idempotency_key
        assert second.idempotency_key
        assert first.idempotency_key != second.idempotency_key

    def test_idempotency_key_survives_serialisation(self) -> None:
        claim = TaskClaim(
            task_id=NotBlankStr("t-2"),
            new_status=NotBlankStr("assigned"),
            idempotency_key=NotBlankStr("pinned-key"),
        )
        roundtrip = TaskClaim.model_validate_json(claim.model_dump_json())
        assert roundtrip.idempotency_key == "pinned-key"


@pytest.fixture
def queue_config() -> QueueConfig:
    return QueueConfig(
        enabled=True,
        ack_wait_seconds=30,
        # Below ack_wait so the working-ack extension fires in time
        # (QueueConfig now enforces heartbeat < ack_wait).
        heartbeat_interval_seconds=10,
        max_deliver=5,
    )


class TestWorkerDedup:
    async def test_first_claim_executes(
        self,
        queue_config: QueueConfig,
    ) -> None:
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        seen = _StubSeenClaims()
        execution_count = 0

        async def executor(_claim: TaskClaim) -> TaskClaimStatus:
            nonlocal execution_count
            execution_count += 1
            return TaskClaimStatus.SUCCESS

        worker = Worker(
            queue_config=queue_config,
            task_queue=_NullTaskQueue(),  # type: ignore[arg-type]
            executor=executor,
            worker_id="test-worker",
            seen_claims=seen,
            clock=clock,
        )
        claim = TaskClaim(
            task_id=NotBlankStr("t-1"),
            new_status=NotBlankStr("assigned"),
        )
        is_completed = await worker._is_completed(claim)
        assert is_completed is False
        assert execution_count == 0
        assert seen.is_completed_calls == [claim.idempotency_key]
        assert seen.mark_seen_calls == []

    async def test_duplicate_claim_short_circuits(
        self,
        queue_config: QueueConfig,
    ) -> None:
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        seen = _StubSeenClaims(completed_keys=frozenset({"already-seen"}))

        async def executor(_claim: TaskClaim) -> TaskClaimStatus:
            return TaskClaimStatus.SUCCESS

        worker = Worker(
            queue_config=queue_config,
            task_queue=_NullTaskQueue(),  # type: ignore[arg-type]
            executor=executor,
            worker_id="test-worker",
            seen_claims=seen,
            clock=clock,
        )
        claim = TaskClaim(
            task_id=NotBlankStr("t-2"),
            new_status=NotBlankStr("assigned"),
            idempotency_key=NotBlankStr("already-seen"),
        )
        is_completed = await worker._is_completed(claim)
        assert is_completed is True

    async def test_no_repo_means_no_dedup(
        self,
        queue_config: QueueConfig,
    ) -> None:
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        worker = Worker(
            queue_config=queue_config,
            task_queue=_NullTaskQueue(),  # type: ignore[arg-type]
            executor=_unused_executor,
            worker_id="test-worker",
            seen_claims=None,
            clock=clock,
        )
        claim = TaskClaim(
            task_id=NotBlankStr("t-3"),
            new_status=NotBlankStr("assigned"),
        )
        is_completed = await worker._is_completed(claim)
        assert is_completed is False


class TestWorkerRunOnceDedupLifecycle:
    """Exercises the actual ``_run_once`` flow end-to-end.

    The previous version of these tests called ``_forget_seen`` /
    ``_mark_completed`` directly, which made them pass even when the
    surrounding orchestration regressed. Driving ``_run_once`` with a
    scripted queue + stub repository asserts the contract the worker
    is supposed to honour: pre-execute read, post-terminal write, and
    no write on RETRY.
    """

    async def test_retry_outcome_does_not_mark_completed(
        self,
        queue_config: QueueConfig,
    ) -> None:
        """RETRY must leave the dedup repo untouched.

        Without this guard the JetStream redelivery would observe a
        ``seen_claims`` row written speculatively before the executor
        ran and ack-and-skip the redelivery, silently dropping the
        task. The defer-mark design closes that by writing the row
        only when the executor returns SUCCESS/FAILED.
        """
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        seen = _StubSeenClaims()
        claim = TaskClaim(
            task_id=NotBlankStr("t-retry"),
            new_status=NotBlankStr("assigned"),
            idempotency_key=NotBlankStr("idem-retry-key"),
        )
        executor_calls = 0

        async def executor(received: TaskClaim) -> TaskClaimStatus:
            nonlocal executor_calls
            executor_calls += 1
            assert received.idempotency_key == "idem-retry-key"
            return TaskClaimStatus.RETRY

        queue = _ScriptedTaskQueue([(claim, _StubRawMessage())])
        worker = Worker(
            queue_config=queue_config,
            task_queue=queue,  # type: ignore[arg-type]
            executor=executor,
            worker_id="test-worker",
            seen_claims=seen,
            clock=clock,
        )

        await worker._run_once()

        assert executor_calls == 1
        assert seen.is_completed_calls == ["idem-retry-key"]
        assert seen.mark_seen_calls == []

    async def test_retry_then_success_executes_twice(
        self,
        queue_config: QueueConfig,
    ) -> None:
        """A RETRY-then-SUCCESS sequence must run the executor twice.

        This pins the regression the dedup row used to cause: the
        first delivery returns RETRY, the second redelivery (same
        idempotency_key) must NOT be suppressed because the first
        outcome never reached a terminal SUCCESS/FAILED.
        """
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        seen = _StubSeenClaims()
        idempotency_key = NotBlankStr("idem-retry-then-success")
        outcomes = iter([TaskClaimStatus.RETRY, TaskClaimStatus.SUCCESS])
        executor_calls = 0

        async def executor(_claim: TaskClaim) -> TaskClaimStatus:
            nonlocal executor_calls
            executor_calls += 1
            return next(outcomes)

        first_delivery = (
            TaskClaim(
                task_id=NotBlankStr("t-1"),
                new_status=NotBlankStr("assigned"),
                idempotency_key=idempotency_key,
            ),
            _StubRawMessage(),
        )
        second_delivery = (
            TaskClaim(
                task_id=NotBlankStr("t-1"),
                new_status=NotBlankStr("assigned"),
                idempotency_key=idempotency_key,
            ),
            _StubRawMessage(),
        )
        queue = _ScriptedTaskQueue([first_delivery, second_delivery])
        worker = Worker(
            queue_config=queue_config,
            task_queue=queue,  # type: ignore[arg-type]
            executor=executor,
            worker_id="test-worker",
            seen_claims=seen,
            clock=clock,
        )

        await worker._run_once()
        await worker._run_once()

        assert executor_calls == 2
        assert seen.mark_seen_calls == [
            {
                "idempotency_key": "idem-retry-then-success",
                "claim_id": "t-1",
                "now": clock.now(),
                "ttl_seconds": worker._dedup_ttl_seconds,
            },
        ]

    async def test_success_marks_and_subsequent_delivery_short_circuits(
        self,
        queue_config: QueueConfig,
    ) -> None:
        """SUCCESS writes the row; redelivery of the same key skips."""
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        seen = _StubSeenClaims()
        idempotency_key = NotBlankStr("idem-success")
        executor_calls = 0

        async def executor(_claim: TaskClaim) -> TaskClaimStatus:
            nonlocal executor_calls
            executor_calls += 1
            return TaskClaimStatus.SUCCESS

        first_delivery = (
            TaskClaim(
                task_id=NotBlankStr("t-ok"),
                new_status=NotBlankStr("assigned"),
                idempotency_key=idempotency_key,
            ),
            _StubRawMessage(),
        )
        second_delivery = (
            TaskClaim(
                task_id=NotBlankStr("t-ok"),
                new_status=NotBlankStr("assigned"),
                idempotency_key=idempotency_key,
            ),
            _StubRawMessage(),
        )
        queue = _ScriptedTaskQueue([first_delivery, second_delivery])
        worker = Worker(
            queue_config=queue_config,
            task_queue=queue,  # type: ignore[arg-type]
            executor=executor,
            worker_id="test-worker",
            seen_claims=seen,
            clock=clock,
        )

        await worker._run_once()
        await worker._run_once()

        assert executor_calls == 1
        assert len(seen.mark_seen_calls) == 1


async def _unused_executor(_claim: TaskClaim) -> TaskClaimStatus:  # pragma: no cover
    return TaskClaimStatus.SUCCESS


# Force pytest-asyncio to pick up the coroutine fixtures above.
__all__ = ("asyncio",)
