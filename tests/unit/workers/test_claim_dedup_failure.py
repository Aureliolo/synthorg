"""Worker dedup-store failure handling: fail-open vs fail-closed.

A transient (retryable) dedup-store error is swallowed so a blip does
not stall the worker; a non-retryable error (schema drift, malformed
query) re-raises so the worker stops loudly instead of silently
degrading exactly-once delivery to at-least-once.
"""

from datetime import UTC, datetime

import pytest
from typeguard import suppress_type_checks

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.workers.claim import TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.worker import Worker
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class _RaisingSeenClaims:
    """Dedup repo whose lookup/mark calls raise a configured error."""

    def __init__(self, *, error: QueryError) -> None:
        self._error = error

    async def is_completed(self, *, idempotency_key: NotBlankStr) -> bool:
        del idempotency_key
        raise self._error

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        del idempotency_key, claim_id, now, ttl_seconds
        raise self._error

    async def prune_expired(self, now: datetime) -> int:
        del now
        return 0


def _queue_config() -> QueueConfig:
    return QueueConfig(
        enabled=True,
        ack_wait_seconds=30,
        heartbeat_interval_seconds=10,
        max_deliver=5,
    )


def _worker(seen: _RaisingSeenClaims) -> Worker:
    async def _executor(_claim: TaskClaim) -> TaskClaimStatus:
        # Never invoked: these tests call the dedup helpers directly.
        return TaskClaimStatus.SUCCESS

    with suppress_type_checks():
        return Worker(
            queue_config=_queue_config(),
            task_queue=object(),  # type: ignore[arg-type]
            executor=_executor,
            worker_id="dedup-test",
            seen_claims=seen,
            clock=FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC)),
        )


def _claim() -> TaskClaim:
    return TaskClaim(
        task_id=NotBlankStr("t-1"),
        new_status=NotBlankStr("assigned"),
    )


class TestMarkCompletedFailure:
    async def test_retryable_mark_is_swallowed(self) -> None:
        worker = _worker(_RaisingSeenClaims(error=QueryError("transient blip")))
        # Fail-open: no raise.
        await worker._mark_completed(_claim())

    async def test_nonretryable_mark_reraises(self) -> None:
        err = ConstraintViolationError("schema drift", constraint="seen_claims_pk")
        worker = _worker(_RaisingSeenClaims(error=err))
        with pytest.raises(QueryError):
            await worker._mark_completed(_claim())


class TestIsCompletedFailure:
    async def test_retryable_lookup_returns_false(self) -> None:
        worker = _worker(_RaisingSeenClaims(error=QueryError("transient blip")))
        assert await worker._is_completed(_claim()) is False

    async def test_nonretryable_lookup_reraises(self) -> None:
        err = ConstraintViolationError("schema drift", constraint="seen_claims_pk")
        worker = _worker(_RaisingSeenClaims(error=err))
        with pytest.raises(QueryError):
            await worker._is_completed(_claim())
