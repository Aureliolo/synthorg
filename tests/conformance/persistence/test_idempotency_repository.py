"""Conformance tests for ``IdempotencyRepository``.

Runs once against SQLite and once against a real Postgres container
via the parametrised ``backend`` fixture so the two implementations
stay in lockstep on the atomic-claim contract.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.idempotency_protocol import (
    IdempotencyClaim,
    IdempotencyOutcome,
)
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
        first = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert first.claim_token is not None
        committed = await backend.idempotency_keys.complete(
            scope=_SCOPE,
            key=key,
            response_body='{"ok": true}',
            response_hash="deadbeef",
            claim_token=first.claim_token,
        )
        assert committed is True
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
        first = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert first.claim_token is not None
        committed = await backend.idempotency_keys.fail(
            scope=_SCOPE,
            key=key,
            claim_token=first.claim_token,
        )
        assert committed is True
        retry = await backend.idempotency_keys.claim(
            scope=_SCOPE,
            key=key,
            ttl_seconds=60,
            now=_now(),
        )
        assert retry.outcome is IdempotencyOutcome.FRESH
        # The reclaim must rotate the token so a stale worker holding
        # the original lease cannot CAS-overwrite this fresh row.
        assert retry.claim_token is not None
        assert retry.claim_token != first.claim_token
        # Verify the CAS guard actually rejects the stale token --
        # otherwise a refactor that dropped ``AND claim_token = ?``
        # or ``AND status = 'in_flight'`` from the UPDATE would let
        # a slow worker overwrite the new lease's row, corrupting
        # the cached response. Both ``complete`` and ``fail`` must
        # return False for the original token, and the new lease's
        # ``in_flight`` status must remain intact.
        stale_complete = await backend.idempotency_keys.complete(
            scope=_SCOPE,
            key=key,
            response_body='{"stale":true}',
            response_hash="stale-hash",
            claim_token=first.claim_token,
        )
        assert stale_complete is False
        stale_fail = await backend.idempotency_keys.fail(
            scope=_SCOPE,
            key=key,
            claim_token=first.claim_token,
        )
        assert stale_fail is False
        # The row must still be in_flight under the NEW lease so the
        # rightful winner can still complete it.
        record = await backend.idempotency_keys.get(scope=_SCOPE, key=key)
        assert record is not None
        assert record.status is IdempotencyOutcome.IN_FLIGHT
        assert record.response_body is None
        assert record.response_hash is None

    async def test_concurrent_claims_only_one_wins(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """``asyncio.gather`` 10 simultaneous claims -- exactly one FRESH.

        Uses a real arrival-counter barrier rather than ``asyncio.sleep(0)``
        so the gate cannot be released until every coroutine has
        actually reached ``await cond.wait_for(...)``. ``sleep(0)``
        only yields once and is not a guaranteed scheduling point on
        every event-loop implementation -- under load some tasks may
        not have reached the wait by the time the gate fires, leaving
        the test asserting a sequential outcome rather than the
        concurrent race that motivates it.
        """
        key = NotBlankStr("key-race")
        n_tasks = 10
        cond = asyncio.Condition()
        arrived = 0

        async def _gated_claim() -> IdempotencyClaim:
            nonlocal arrived
            async with cond:
                arrived += 1
                if arrived == n_tasks:
                    # Last arrival releases everyone simultaneously.
                    cond.notify_all()
                else:
                    await cond.wait_for(lambda: arrived == n_tasks)
            return await backend.idempotency_keys.claim(
                scope=_SCOPE,
                key=key,
                ttl_seconds=60,
                now=_now(),
            )

        results = await asyncio.gather(
            *[_gated_claim() for _ in range(n_tasks)],
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
