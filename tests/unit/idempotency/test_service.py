"""Tests for ``IdempotencyService.run_idempotent``.

The repository-layer conformance suite covers the atomic claim
contract; this file covers the service-layer wrapper: callback
execution exactly once on FRESH, cached response on COMPLETED, and
fail-marker on callback exception.
"""

from datetime import UTC, datetime
from types import ModuleType
from typing import override

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.idempotency import IdempotencyService
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

    #: Token issued on FRESH claims; tests assert that the service
    #: forwards this exact value to ``complete`` / ``fail`` so a
    #: future regression that drops the lease token surfaces here.
    FRESH_TOKEN: str = "test-token"

    def __init__(self, *, initial_outcome: IdempotencyOutcome | None = None) -> None:
        self.next_outcome = initial_outcome or IdempotencyOutcome.FRESH
        self.cached_response: str | None = None
        self.completes: list[tuple[str, str, str]] = []
        self.fails: list[tuple[str, str, str]] = []
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
        if self.next_outcome is IdempotencyOutcome.FRESH:
            return IdempotencyClaim(
                outcome=IdempotencyOutcome.FRESH,
                claim_token=NotBlankStr(self.FRESH_TOKEN),
            )
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
        claim_token: NotBlankStr,
    ) -> bool:
        del scope, key
        # Capture ``claim_token`` so tests can assert the service
        # forwards the FRESH lease token verbatim. Token CAS is the
        # core race fix for stale-worker overwrites; if the service
        # ever drops the forward, ``complete`` would silently degrade
        # to "complete by (scope, key)" and re-introduce the race.
        self.completes.append((response_body, response_hash, claim_token))
        self.cached_response = response_body
        self.next_outcome = IdempotencyOutcome.COMPLETED
        return True

    async def fail(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        claim_token: NotBlankStr,
    ) -> bool:
        # Capture token for the same reason as ``complete``.
        self.fails.append((str(scope), str(key), claim_token))
        self.next_outcome = IdempotencyOutcome.FAILED
        return True

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

    async def cb() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": 42}

    outcome = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert calls == 1
    assert outcome.fresh is True
    assert outcome.timed_out is False
    assert outcome.result == {"status": "ok", "n": 42}
    assert len(repo.completes) == 1
    body, digest, forwarded_token = repo.completes[0]
    assert "status" in body
    assert "ok" in body
    assert len(digest) == 64  # SHA-256 hex
    # Token CAS regression guard: the FRESH lease token issued by the
    # repo MUST be forwarded verbatim to ``complete`` -- a service
    # that drops the token would silently degrade the lease contract.
    assert forwarded_token == _FakeRepo.FRESH_TOKEN


async def test_run_idempotent_returns_cached_on_completed_claim() -> None:
    repo = _FakeRepo(initial_outcome=IdempotencyOutcome.COMPLETED)
    repo.cached_response = '{"status": "cached"}'
    svc = IdempotencyService(repo)

    calls = 0

    async def cb() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "ok"}

    outcome = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert calls == 0, "callback must not run on cached claim"
    assert outcome.fresh is False
    assert outcome.timed_out is False
    assert outcome.result == {"status": "cached"}


async def test_run_idempotent_marks_failed_when_callback_raises() -> None:
    repo = _FakeRepo(initial_outcome=IdempotencyOutcome.FRESH)
    svc = IdempotencyService(repo)

    class _BoomError(Exception):
        pass

    async def cb() -> dict[str, object]:
        raise _BoomError

    with pytest.raises(_BoomError):
        await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert len(repo.fails) == 1
    fail_scope, fail_key, fail_token = repo.fails[0]
    assert (fail_scope, fail_key) == (str(_SCOPE), str(_KEY))
    # Same token-forwarding regression guard as the success path.
    assert fail_token == _FakeRepo.FRESH_TOKEN
    assert len(repo.completes) == 0


class _DeterministicClock:
    """Stub Clock injected via the service constructor's ``clock`` kwarg.

    Tracks a virtual-time float that advances when the service awaits
    asyncio.sleep (which we also stub out so polling deadlines progress
    without real wall-clock waits).
    """

    def __init__(self) -> None:
        self.now_seconds = 0.0

    def now(self) -> datetime:
        from datetime import UTC
        from datetime import datetime as _dt

        return _dt.fromtimestamp(self.now_seconds, tz=UTC)

    def monotonic(self) -> float:
        return self.now_seconds

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.now_seconds += seconds


def _install_deterministic_clock(
    monkeypatch: pytest.MonkeyPatch,
    svc_mod: ModuleType,
) -> _DeterministicClock:
    """Stub asyncio.sleep so the service's polling loop progresses
    against the injected ``_DeterministicClock`` without real waits.

    Returns the clock instance; the test passes it via the service
    constructor's ``clock`` kwarg, then reads/writes ``now_seconds``
    to verify timing-dependent behaviour.
    """
    clock = _DeterministicClock()

    async def _fake_sleep(delay: float) -> None:
        # Negative or zero stays a real no-op; positive advances the
        # virtual clock so deadline arithmetic in the service-under-
        # test progresses without a real wall-clock sleep.
        if delay > 0:
            clock.now_seconds += delay

    monkeypatch.setattr(svc_mod.asyncio, "sleep", _fake_sleep)
    return clock


async def test_run_idempotent_in_flight_returns_none_after_poll_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the repo reports IN_FLIGHT and ``get`` keeps returning a
    record stuck in the in-flight state, the service polls then
    returns (None, False) so the controller can surface 409."""
    from synthorg.idempotency import service as svc_mod

    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS", 0.005)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS", 0.01)
    clock = _install_deterministic_clock(monkeypatch, svc_mod)

    class _StuckRepo(_FakeRepo):
        @override
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
    svc = svc_mod.IdempotencyService(repo, clock=clock)

    async def cb() -> dict[str, object]:
        msg = "callback must not run when claim is in-flight"
        raise AssertionError(msg)

    outcome = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert outcome.fresh is False
    # Discriminated timeout: ``timed_out`` is True so callers can
    # distinguish this from a callback that legitimately returned
    # ``None``.
    assert outcome.timed_out is True
    assert outcome.result is None


async def test_run_idempotent_in_flight_resolves_to_completed_via_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second request that polls during an in-flight claim picks up
    the cached response once the first request completes."""
    from synthorg.idempotency import service as svc_mod

    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS", 0.005)
    monkeypatch.setattr(svc_mod, "_IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS", 0.01)
    clock = _install_deterministic_clock(monkeypatch, svc_mod)

    poll_count = 0

    class _ResolvingRepo(_FakeRepo):
        @override
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
                response_hash="resolved-hash",
                response_body='{"status": "resolved"}',
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC),
            )

    repo = _ResolvingRepo(initial_outcome=IdempotencyOutcome.IN_FLIGHT)
    svc = svc_mod.IdempotencyService(repo, clock=clock)

    async def cb() -> dict[str, object]:
        msg = "callback must not run when claim is in-flight"
        raise AssertionError(msg)

    outcome = await svc.run_idempotent(scope=_SCOPE, key=_KEY, callback=cb)
    assert outcome.fresh is False
    assert outcome.timed_out is False
    assert outcome.result == {"status": "resolved"}


async def test_cleanup_expired_delegates_to_repository() -> None:
    repo = _FakeRepo()
    svc = IdempotencyService(repo)
    removed = await svc.cleanup_expired()
    assert removed == 0
    assert len(repo.cleanup_calls) == 1
