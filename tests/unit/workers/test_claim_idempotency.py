"""Unit coverage for TaskClaim idempotency_key + worker dedup.

The worker-side dedup test uses a stub ``SeenClaimsRepository`` that
records every ``mark_seen`` call so we can assert the exact protocol
contract without spinning up a real persistence backend.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.workers.claim import TaskClaim, TaskClaimStatus
from synthorg.workers.config import QueueConfig
from synthorg.workers.worker import Worker
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class _StubSeenClaims:
    """Records ``mark_seen`` invocations; configurable per-key insert outcome."""

    def __init__(
        self,
        *,
        outcomes: dict[str, bool] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcomes = outcomes or {}

    async def mark_seen(
        self,
        *,
        idempotency_key: NotBlankStr,
        claim_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        self.calls.append(
            {
                "idempotency_key": str(idempotency_key),
                "claim_id": str(claim_id),
                "now": now,
                "ttl_seconds": ttl_seconds,
            },
        )
        return self._outcomes.get(str(idempotency_key), True)

    async def prune_expired(self, now: datetime) -> int:
        return 0


class _NullTaskQueue:
    """Stand-in for ``JetStreamTaskQueue`` -- worker never calls these."""

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
        is_dup = await worker._is_duplicate(claim)
        assert is_dup is False
        assert execution_count == 0
        assert len(seen.calls) == 1
        assert seen.calls[0]["idempotency_key"] == claim.idempotency_key

    async def test_duplicate_claim_short_circuits(
        self,
        queue_config: QueueConfig,
    ) -> None:
        clock = FakeClock(start=datetime(2026, 5, 13, tzinfo=UTC))
        seen = _StubSeenClaims(outcomes={"already-seen": False})

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
        is_dup = await worker._is_duplicate(claim)
        assert is_dup is True

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
        is_dup = await worker._is_duplicate(claim)
        assert is_dup is False


async def _unused_executor(_claim: TaskClaim) -> TaskClaimStatus:  # pragma: no cover
    return TaskClaimStatus.SUCCESS


# Force pytest-asyncio to pick up the coroutine fixtures above.
__all__ = ("asyncio",)
