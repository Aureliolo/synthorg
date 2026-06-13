"""Tests for SSE periodic revalidation.

Mirrors the WS revalidation surface: every long-lived stream must
close on user_deleted / role_demoted / session_revoked, and absorb
transient persistence failures through the SHARED sliding-window
limiter (``api.auth_revalidate_window_seconds`` /
``api.auth_revalidate_max_failures``) rather than a streak counter,
so a flaky backend interleaving one success cannot keep a stale-auth
stream open.
"""

import json
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from typeguard import suppress_type_checks

from synthorg.api.controllers.events._sse import (
    _run_revalidation_tick,
    _serialise_stream_event,
    _user_revocation_reason,
)
from synthorg.api.state import AppState
from synthorg.communication.event_stream.types import AgUiEventType, StreamEvent
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod, User
from synthorg.core.auth.roles import HumanRole
from synthorg.engine.classification.sinks import _SlidingWindowRateLimiter
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import persistence_of
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_user(role: HumanRole = HumanRole.CEO) -> User:
    now = datetime.now(UTC)
    return User(
        id="u-001",
        username="alice",
        password_hash=("$argon2id$v=19$m=65536,t=3,p=4$cGVwcGVy$abcd1234"),
        role=role,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )


def _make_app_state(  # noqa: PLR0913
    *,
    persisted_user: User | None,
    raise_on_get: bool = False,
    has_session_store: bool = False,
    is_revoked: bool = False,
    auth_revalidate_window_seconds: int = 60,
    auth_revalidate_max_failures: int = 5,
) -> AppState:
    """Build an AppState with the revalidation-path slices + knobs wired.

    ``_user_revocation_reason`` reads the users repo via
    ``persistence_of`` and the session store via
    ``slice(ApiCoreStateSlice).session_store``; the failure-tolerance
    knobs are surviving cross-cutting primitives on ``AppState``.
    """
    users_repo = AsyncMock()
    if raise_on_get:
        users_repo.get.side_effect = RuntimeError("transient db blip")
    else:
        users_repo.get.return_value = persisted_user
    persistence = mock_of[PersistenceBackend](users=users_repo)
    session_store = (
        type("Ss", (), {"is_revoked": lambda _self, _jti: is_revoked})()
        if has_session_store
        else None
    )
    app_state = make_app_state(persistence=persistence, session_store=session_store)
    # Revalidation failure tolerance is resolved from these AppState
    # primitives (shared with the WS loop), not config_resolver.
    ws_limits = app_state.ws_auth_limits
    ws_limits.set_auth_revalidate_window_seconds(auth_revalidate_window_seconds)
    ws_limits.set_auth_revalidate_max_failures(auth_revalidate_max_failures)
    return app_state


async def test_serialise_stream_event_includes_id_field() -> None:
    """The SSE frame carries the event id so the browser can resume via
    the ``Last-Event-ID`` header on reconnect."""
    event = StreamEvent(
        id="evt-42",
        type=AgUiEventType.RUN_STARTED,
        timestamp=datetime.now(UTC),
        session_id="sess-1",
    )
    frame = await _serialise_stream_event(event, "sess-1")
    assert frame is not None
    assert frame["id"] == "evt-42"
    assert frame["event"] == "run_started"
    assert json.loads(frame["data"])["id"] == "evt-42"


async def test_revocation_reason_returns_user_deleted_when_user_missing() -> None:
    state = _make_app_state(persisted_user=None)
    reason, ok = await _user_revocation_reason(state, "u-001", None)
    assert ok is True
    assert reason == "user_deleted"


async def test_revocation_reason_returns_role_demoted_for_system_role() -> None:
    demoted = _make_user(role=HumanRole.SYSTEM)
    state = _make_app_state(persisted_user=demoted)
    reason, ok = await _user_revocation_reason(state, "u-001", None)
    assert ok is True
    assert reason == "role_demoted"


async def test_revocation_reason_returns_none_for_active_user() -> None:
    user = _make_user(role=HumanRole.CEO)
    state = _make_app_state(persisted_user=user)
    reason, ok = await _user_revocation_reason(state, "u-001", None)
    assert ok is True
    assert reason is None


async def test_revocation_reason_signals_not_ok_on_transient_failure() -> None:
    state = _make_app_state(persisted_user=None, raise_on_get=True)
    reason, ok = await _user_revocation_reason(state, "u-001", None)
    assert ok is False
    assert reason is None


async def test_revocation_reason_session_revoked_kicks_stream() -> None:
    """A revoked JTI on an otherwise-active user surfaces as
    ``session_revoked`` so the SSE stream tears down within one
    revalidation interval rather than waiting for token expiry."""
    user = _make_user(role=HumanRole.CEO)
    state = _make_app_state(
        persisted_user=user,
        has_session_store=True,
        is_revoked=True,
    )
    reason, ok = await _user_revocation_reason(state, "u-001", "jti-123")
    assert ok is True
    assert reason == "session_revoked"


async def test_sse_event_stream_emits_revoked_when_role_demoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: feed the generator a role-demoted user mid-stream
    and assert it yields a final 'revoked' event before terminating."""
    from synthorg.api.controllers.events import _sse as events_mod

    # Fast-path: shrink keepalive + revalidate cadence so the test
    # does not wait minutes for the revalidation tick.
    monkeypatch.setattr(events_mod, "_SSE_KEEPALIVE_FALLBACK_SECONDS", 0.01)
    monkeypatch.setattr(events_mod, "AUTH_REVALIDATE_INTERVAL_SECONDS", 0.02)

    demoted = _make_user(role=HumanRole.SYSTEM)
    app_state = _make_app_state(persisted_user=demoted)

    class _FakeQueue:
        async def get(self) -> object:
            import asyncio

            return await asyncio.Event().wait()

    class _FakeHub:
        async def subscribe(self, _session_id: str) -> _FakeQueue:
            return _FakeQueue()

        async def unsubscribe(self, _session_id: str, _queue: _FakeQueue) -> None:
            pass

    user = AuthenticatedUser(
        user_id="u-001",
        username="alice",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )
    # ``_FakeHub`` is a behavioural subscribe/unsubscribe stand-in for a
    # concrete ``EventStreamHub``; the runtime check is suppressed at the same
    # boundary as the static ``# type: ignore[arg-type]`` (the test verifies the
    # revoked-event emission, not hub type conformance). The suppression spans
    # the iteration because typeguard checks the generator's args on first
    # ``__anext__``, not at construction.
    saw_revoked = False
    iterations = 0
    # Real asyncio.wait_for drives the loop sleep, so the cap is a
    # wall-clock safety net: the role-demoted check fires once per
    # AUTH_REVALIDATE_INTERVAL_SECONDS; 200 iterations at 20ms gives
    # 4s of headroom for slow CI without masking a regression.
    iteration_cap = 200
    with suppress_type_checks():
        gen = events_mod._sse_event_stream(
            _FakeHub(),  # type: ignore[arg-type]
            "sess-1",
            app_state=app_state,
            user=user,
        )
        async for event in gen:
            iterations += 1
            if event.get("event") == "revoked":
                payload = json.loads(event["data"])
                assert payload["reason"] == "role_demoted"
                saw_revoked = True
                break
            assert iterations < iteration_cap
    assert saw_revoked, "SSE stream never emitted the revoked event"


def _make_auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="u-001",
        username="alice",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )


async def test_revalidation_tick_tolerates_failures_within_window() -> None:
    """``max_failures`` transient errors inside the window are absorbed
    (tick returns ``None``); the stream keeps running."""
    state = _make_app_state(persisted_user=None, raise_on_get=True)
    limiter = _SlidingWindowRateLimiter(max_events=3, window_seconds=60.0)
    user = _make_auth_user()

    for _ in range(3):
        revoked = await _run_revalidation_tick(
            app_state=state,
            user=user,
            failure_limiter=limiter,
        )
        assert revoked is None


async def test_revalidation_tick_revokes_when_window_saturates() -> None:
    """The failure that exceeds the window budget tears the stream
    down with a backend-unavailable frame -- and, unlike a streak
    counter, an interleaved success does NOT reset the budget."""
    state = _make_app_state(persisted_user=None, raise_on_get=True)
    limiter = _SlidingWindowRateLimiter(max_events=3, window_seconds=60.0)
    user = _make_auth_user()

    for _ in range(3):
        assert (
            await _run_revalidation_tick(
                app_state=state,
                user=user,
                failure_limiter=limiter,
            )
            is None
        )

    revoked = await _run_revalidation_tick(
        app_state=state,
        user=user,
        failure_limiter=limiter,
    )
    assert revoked is not None
    assert revoked["event"] == "revoked"
    assert json.loads(revoked["data"])["reason"] == "backend_unavailable"


async def test_interleaved_success_does_not_reset_failure_budget() -> None:
    """The core regression the sliding window exists to prevent: a
    transient backend that returns one good response between failure
    clusters must NOT reset the budget (a streak counter would). With
    max_events=3, the sequence fail, ok, fail, ok, fail, fail must
    still revoke on the 4th failure despite the interleaved successes.
    """
    healthy = _make_user(role=HumanRole.CEO)
    state = _make_app_state(persisted_user=healthy)
    # Alternate transient failure / healthy read; the limiter only
    # ever sees the failures (ok ticks return None without taking).
    cast(AsyncMock, persistence_of(state).users.get).side_effect = [
        RuntimeError("blip"),
        healthy,
        RuntimeError("blip"),
        healthy,
        RuntimeError("blip"),
        RuntimeError("blip"),
    ]
    limiter = _SlidingWindowRateLimiter(max_events=3, window_seconds=60.0)
    user = _make_auth_user()

    # F, ok, F, ok, F  -> 3 admitted failures, never revoked.
    for _ in range(5):
        assert (
            await _run_revalidation_tick(
                app_state=state,
                user=user,
                failure_limiter=limiter,
            )
            is None
        )

    # 4th failure exceeds the window despite the two interleaved
    # successes -> revoke (a reset-on-success streak would never).
    revoked = await _run_revalidation_tick(
        app_state=state,
        user=user,
        failure_limiter=limiter,
    )
    assert revoked is not None
    assert json.loads(revoked["data"])["reason"] == "backend_unavailable"
