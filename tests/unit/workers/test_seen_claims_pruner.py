"""Unit tests for the periodic seen_claims pruner.

Exercised against the real SQLite ``SeenClaimsRepository`` so the
prune SQL (and the expiry index it relies on) is what is tested, not a
stub.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.workers.seen_claims_pruner import SeenClaimsPruner
from tests._shared.fake_clock import FakeClock
from tests._shared.persistence import make_sqlite_seen_claims

pytestmark = pytest.mark.unit

_HARD_CAP_SECONDS: Final[float] = 5.0
_POLL_SECONDS: Final[float] = 0.01
_BASE: Final[datetime] = datetime(2026, 1, 1, tzinfo=UTC)


async def test_prune_once_removes_only_expired_rows() -> None:
    async with make_sqlite_seen_claims() as repo:
        await repo.mark_seen(
            idempotency_key=NotBlankStr("expired"),
            claim_id=NotBlankStr("task-old"),
            now=_BASE,
            ttl_seconds=1.0,
        )
        await repo.mark_seen(
            idempotency_key=NotBlankStr("fresh"),
            claim_id=NotBlankStr("task-new"),
            now=_BASE,
            ttl_seconds=10_000.0,
        )
        # Pruner clock well past the short TTL but before the long one.
        pruner = SeenClaimsPruner(
            seen_claims=repo,
            interval_seconds=60.0,
            clock=FakeClock(start=_BASE + timedelta(seconds=100)),
        )

        removed = await pruner._prune_once()

        assert removed == 1
        assert await repo.is_completed(idempotency_key=NotBlankStr("expired")) is False
        assert await repo.is_completed(idempotency_key=NotBlankStr("fresh")) is True


async def test_loop_prunes_then_stops_cleanly() -> None:
    async with make_sqlite_seen_claims() as repo:
        await repo.mark_seen(
            idempotency_key=NotBlankStr("doomed"),
            claim_id=NotBlankStr("task-doomed"),
            now=_BASE,
            ttl_seconds=1.0,
        )
        pruner = SeenClaimsPruner(
            seen_claims=repo,
            interval_seconds=30.0,
            clock=FakeClock(start=_BASE + timedelta(seconds=100)),
        )
        await pruner.start()
        with pytest.raises(RuntimeError, match="already running"):
            await pruner.start()

        # Bounded poll: asyncio.timeout is the hard cap; no Event to await.
        async with asyncio.timeout(_HARD_CAP_SECONDS):
            while await repo.is_completed(  # noqa: ASYNC110
                idempotency_key=NotBlankStr("doomed"),
            ):
                await asyncio.sleep(_POLL_SECONDS)

        await pruner.stop()
        await pruner.stop()  # idempotent
        assert pruner.is_running is False
