"""Tests for ``IdempotencyService.run_idempotent`` (#1599 §3 service layer).

The repository-layer conformance suite covers the atomic claim
contract; this file covers the service-layer wrapper: callback
execution exactly once on FRESH, cached response on COMPLETED, and
fail-marker on callback exception.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.api.services.idempotency_service import IdempotencyService
from synthorg.core.types import NotBlankStr
from synthorg.persistence.idempotency_protocol import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
)

pytestmark = pytest.mark.unit

_SCOPE = NotBlankStr("test")
_KEY = NotBlankStr("key-1")


class _FakeRepo:
    """Minimal in-memory ``IdempotencyRepository`` stub.

    The conformance suite already covers the atomic-claim semantics
    against real backends; this fake just records call ordering so
    the service-layer assertions stay focused.
    """

    def __init__(self, *, initial_outcome: IdempotencyOutcome | None = None) -> None:
        self.next_outcome = initial_outcome or IdempotencyOutcome.FRESH
        self.cached_response: str | None = None
        self.completes: list[tuple[str, str]] = []
        self.fails: list[tuple[str, str]] = []
        self.cleanup_calls: list[datetime] = []

    async def claim(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        ttl_seconds: int,
        now: datetime,
    ) -> IdempotencyClaim:
        del scope, key
        return IdempotencyClaim(
            outcome=self.next_outcome,
            cached_response=self.cached_response,
        )

    async def complete(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        response_body: str,
        response_hash: str,
    ) -> None:
        del scope, key
        self.completes.append((response_body, response_hash))
        self.cached_response = response_body
        self.next_outcome = IdempotencyOutcome.COMPLETED

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> None:
        self.fails.append((str(scope), str(key)))
        self.next_outcome = IdempotencyOutcome.FAILED

    async def get(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> IdempotencyRecord | None:
        del scope, key
        return None

    async def cleanup_expired(self, now: datetime) -> int:
        self.cleanup_calls.append(now)
        return 0


async def test_run_idempotent_executes_callback_on_fresh_claim() -> None:
    repo = _FakeRepo(initial_outcome=IdempotencyOutcome.FRESH)
    svc = IdempotencyService(repo)

    calls = 0

    async def cb() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": 42}

    result, fresh = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert calls == 1
    assert fresh is True
    assert result == {"status": "ok", "n": 42}
    assert len(repo.completes) == 1
    body, digest = repo.completes[0]
    assert "status" in body
    assert "ok" in body
    assert len(digest) == 64  # SHA-256 hex


async def test_run_idempotent_returns_cached_on_completed_claim() -> None:
    repo = _FakeRepo(initial_outcome=IdempotencyOutcome.COMPLETED)
    repo.cached_response = '{"status": "cached"}'
    svc = IdempotencyService(repo)

    calls = 0

    async def cb() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"status": "ok"}

    result, fresh = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert calls == 0, "callback must not run on cached claim"
    assert fresh is False
    assert result == {"status": "cached"}


async def test_run_idempotent_marks_failed_when_callback_raises() -> None:
    repo = _FakeRepo(initial_outcome=IdempotencyOutcome.FRESH)
    svc = IdempotencyService(repo)

    class _BoomError(Exception):
        pass

    async def cb() -> dict[str, Any]:
        raise _BoomError

    with pytest.raises(_BoomError):
        await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert len(repo.fails) == 1
    assert repo.fails[0] == (str(_SCOPE), str(_KEY))
    assert len(repo.completes) == 0


async def test_run_idempotent_in_flight_returns_none_after_poll_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the repo reports IN_FLIGHT and ``get`` keeps returning a
    record stuck in the in-flight state, the service polls then
    returns (None, False) so the controller can surface 409."""
    from synthorg.api.services import idempotency_service as svc_mod

    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS", 0.005)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS", 0.01)

    class _StuckRepo(_FakeRepo):
        async def get(
            self,
            *,
            scope: NotBlankStr,
            key: NotBlankStr,
        ) -> IdempotencyRecord | None:
            del scope, key
            return IdempotencyRecord(
                scope=NotBlankStr("test"),
                key=NotBlankStr("key-1"),
                status=IdempotencyOutcome.IN_FLIGHT,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC),
            )

    repo = _StuckRepo(initial_outcome=IdempotencyOutcome.IN_FLIGHT)
    svc = svc_mod.IdempotencyService(repo)

    async def cb() -> dict[str, Any]:
        msg = "callback must not run when claim is in-flight"
        raise AssertionError(msg)

    result, fresh = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert fresh is False
    assert result is None


async def test_run_idempotent_in_flight_resolves_to_completed_via_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second request that polls during an in-flight claim picks up
    the cached response once the first request completes."""
    from synthorg.api.services import idempotency_service as svc_mod

    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS", 0.005)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS", 0.01)

    poll_count = 0

    class _ResolvingRepo(_FakeRepo):
        async def get(
            self,
            *,
            scope: NotBlankStr,
            key: NotBlankStr,
        ) -> IdempotencyRecord | None:
            del scope, key
            nonlocal poll_count
            poll_count += 1
            if poll_count < 2:
                return IdempotencyRecord(
                    scope=NotBlankStr("test"),
                    key=NotBlankStr("key-1"),
                    status=IdempotencyOutcome.IN_FLIGHT,
                    created_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC),
                )
            return IdempotencyRecord(
                scope=NotBlankStr("test"),
                key=NotBlankStr("key-1"),
                status=IdempotencyOutcome.COMPLETED,
                response_body='{"status": "resolved"}',
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC),
            )

    repo = _ResolvingRepo(initial_outcome=IdempotencyOutcome.IN_FLIGHT)
    svc = svc_mod.IdempotencyService(repo)

    async def cb() -> dict[str, Any]:
        msg = "callback must not run when claim is in-flight"
        raise AssertionError(msg)

    result, fresh = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert fresh is False
    assert result == {"status": "resolved"}


async def test_cleanup_expired_delegates_to_repository() -> None:
    repo = _FakeRepo()
    svc = IdempotencyService(repo)
    removed = await svc.cleanup_expired()
    assert removed == 0
    assert len(repo.cleanup_calls) == 1
