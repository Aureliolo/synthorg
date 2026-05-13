"""Conformance tests for ``SeenClaimsRepository``.

Runs once against SQLite and once against a real Postgres container
via the parametrised ``backend`` fixture so the two implementations
stay in lockstep on the worker dedup contract.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(UTC)


class TestSeenClaimsMarkSeen:
    async def test_first_mark_returns_true(
        self,
        backend: PersistenceBackend,
    ) -> None:
        inserted = await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-first"),
            claim_id=NotBlankStr("task-1"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert inserted is True

    async def test_second_mark_returns_false(
        self,
        backend: PersistenceBackend,
    ) -> None:
        key = NotBlankStr("idem-dup")
        first = await backend.seen_claims.mark_seen(
            idempotency_key=key,
            claim_id=NotBlankStr("task-2"),
            now=_now(),
            ttl_seconds=60.0,
        )
        second = await backend.seen_claims.mark_seen(
            idempotency_key=key,
            claim_id=NotBlankStr("task-2"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert first is True
        assert second is False

    async def test_distinct_keys_both_insert(
        self,
        backend: PersistenceBackend,
    ) -> None:
        first = await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-a"),
            claim_id=NotBlankStr("task-a"),
            now=_now(),
            ttl_seconds=60.0,
        )
        second = await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-b"),
            claim_id=NotBlankStr("task-b"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert first is True
        assert second is True


class TestSeenClaimsPruneExpired:
    async def test_prune_removes_expired_rows(
        self,
        backend: PersistenceBackend,
    ) -> None:
        seeded_at = _now() - timedelta(seconds=120)
        await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-old"),
            claim_id=NotBlankStr("task-old"),
            now=seeded_at,
            ttl_seconds=1.0,
        )
        removed = await backend.seen_claims.prune_expired(_now())
        assert removed >= 1
        # Re-marking should now succeed because the row was pruned.
        fresh = await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-old"),
            claim_id=NotBlankStr("task-old"),
            now=_now(),
            ttl_seconds=60.0,
        )
        assert fresh is True

    async def test_prune_leaves_live_rows(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-live"),
            claim_id=NotBlankStr("task-live"),
            now=_now(),
            ttl_seconds=600.0,
        )
        removed = await backend.seen_claims.prune_expired(_now())
        assert removed == 0
        # Re-marking the live row still observes the duplicate.
        duplicate = await backend.seen_claims.mark_seen(
            idempotency_key=NotBlankStr("idem-live"),
            claim_id=NotBlankStr("task-live"),
            now=_now(),
            ttl_seconds=600.0,
        )
        assert duplicate is False
