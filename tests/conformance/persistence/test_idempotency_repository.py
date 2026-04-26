"""Conformance tests for ``IdempotencyRepository`` (#1599).

Runs once against SQLite and once against a real Postgres container
via the parametrised ``backend`` fixture so the two implementations
stay in lockstep on the atomic-claim contract.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.idempotency_protocol import IdempotencyOutcome
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


_SCOPE = NotBlankStr("test_scope")


def _now() -> datetime:
    return datetime.now(UTC)


class TestIdempotencyClaim:
    async def test_first_claim_returns_fresh(
        self,
        backend: PersistenceBackend,
    ) -> None:
        claim = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=NotBlankStr("key-fresh"),
            ttl_seconds=60,
            now=_now(),
        )
        assert claim.outcome is IdempotencyOutcome.FRESH

    async def test_second_claim_returns_in_flight(
        self,
        backend: PersistenceBackend,
    ) -> None:
        key = NotBlankStr("key-in-flight")
        first = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        second = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert first.outcome is IdempotencyOutcome.FRESH
        assert second.outcome is IdempotencyOutcome.IN_FLIGHT

    async def test_completed_claim_returns_cached_response(
        self,
        backend: PersistenceBackend,
    ) -> None:
        key = NotBlankStr("key-completed")
        await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        await backend.idempotency_keys.complete(
            scope=_SCOPE,
            key=key,
            response_body='{"ok": true}',
            response_hash="deadbeef",
        )
        claim = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert claim.outcome is IdempotencyOutcome.COMPLETED
        assert claim.cached_response == '{"ok": true}'

    async def test_expired_claim_returns_fresh(
        self,
        backend: PersistenceBackend,
    ) -> None:
        key = NotBlankStr("key-expired")
        past = _now() - timedelta(seconds=10)
        await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=1,
            now=past,
        )
        claim = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert claim.outcome is IdempotencyOutcome.FRESH

    async def test_failed_claim_can_be_re_claimed(
        self,
        backend: PersistenceBackend,
    ) -> None:
        key = NotBlankStr("key-failed-retry")
        await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        await backend.idempotency_keys.fail(scope=_SCOPE, key=key)
        retry = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert retry.outcome is IdempotencyOutcome.FRESH

    async def test_concurrent_claims_only_one_wins(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """``asyncio.gather`` 10 simultaneous claims -- exactly one FRESH."""
        key = NotBlankStr("key-race")
        results = await asyncio.gather(
            *[
                backend.idempotency_keys.claim(
                    scope=_SCOPE,
                    key=key,
                    ttl_seconds=60,
                    now=_now(),
                )
                for _ in range(10)
            ],
        )
        fresh = [r for r in results if r.outcome is IdempotencyOutcome.FRESH]
        in_flight = [r for r in results if r.outcome is IdempotencyOutcome.IN_FLIGHT]
        assert len(fresh) == 1
        assert len(in_flight) == 9

    async def test_cleanup_expired_drops_old_rows(
        self,
        backend: PersistenceBackend,
    ) -> None:
        past = _now() - timedelta(seconds=10)
        await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=NotBlankStr("k-old"),
            ttl_seconds=1,
            now=past,
        )
        await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=NotBlankStr("k-new"),
            ttl_seconds=600,
            now=_now(),
        )
        removed = await backend.idempotency_keys.cleanup_expired(_now())
        assert removed >= 1
        # The new (unexpired) row should still be claimable as IN_FLIGHT
        # since it was just claimed.
        claim = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=NotBlankStr("k-new"),
            ttl_seconds=60,
            now=_now(),
        )
        assert claim.outcome is IdempotencyOutcome.IN_FLIGHT
