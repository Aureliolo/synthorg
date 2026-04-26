"""Tests for SSE periodic revalidation (#1599 §6.2).

Mirrors the WS revalidation test surface: every long-lived stream
must close on user_deleted / role_demoted, and tolerate up to three
consecutive transient persistence failures before terminating with a
backend-unavailable signal.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.auth.models import User
from synthorg.api.controllers.events import _user_revocation_reason
from synthorg.api.guards import HumanRole

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


class _FakeAppState:
    def __init__(
        self,
        *,
        persisted_user: User | None,
        raise_on_get: bool = False,
    ) -> None:
        users_repo = AsyncMock()
        if raise_on_get:
            users_repo.get.side_effect = RuntimeError("transient db blip")
        else:
            users_repo.get.return_value = persisted_user
        self.persistence = type("Pst", (), {"users": users_repo})()


async def test_revocation_reason_returns_user_deleted_when_user_missing() -> None:
    state = _FakeAppState(persisted_user=None)
    reason, ok = await _user_revocation_reason(state, "u-001")  # type: ignore[arg-type]
    assert ok is True
    assert reason == "user_deleted"


async def test_revocation_reason_returns_role_demoted_for_system_role() -> None:
    demoted = _make_user(role=HumanRole.SYSTEM)
    state = _FakeAppState(persisted_user=demoted)
    reason, ok = await _user_revocation_reason(state, "u-001")  # type: ignore[arg-type]
    assert ok is True
    assert reason == "role_demoted"


async def test_revocation_reason_returns_none_for_active_user() -> None:
    user = _make_user(role=HumanRole.CEO)
    state = _FakeAppState(persisted_user=user)
    reason, ok = await _user_revocation_reason(state, "u-001")  # type: ignore[arg-type]
    assert ok is True
    assert reason is None


async def test_revocation_reason_signals_not_ok_on_transient_failure() -> None:
    state = _FakeAppState(persisted_user=None, raise_on_get=True)
    reason, ok = await _user_revocation_reason(state, "u-001")  # type: ignore[arg-type]
    assert ok is False
    assert reason is None


async def test_sse_event_stream_emits_revoked_when_role_demoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: feed the generator a role-demoted user mid-stream
    and assert it yields a final 'revoked' event before terminating."""
    import json

    from synthorg.api.auth.models import AuthenticatedUser, AuthMethod
    from synthorg.api.controllers import events as events_mod

    # Fast-path: shrink keepalive + revalidate cadence so the test
    # doesn't wait minutes for the revalidation tick.
    monkeypatch.setattr(events_mod, "_SSE_KEEPALIVE_SECONDS", 0.01)
    monkeypatch.setattr(events_mod, "SSE_REVALIDATE_INTERVAL_SECONDS", 0.02)

    demoted = _make_user(role=HumanRole.SYSTEM)
    app_state = _FakeAppState(persisted_user=demoted)

    class _FakeQueue:
        async def get(self) -> Any:
            # Force a TimeoutError by sleeping forever; ``asyncio.wait_for``
            # will fire the keepalive branch on its 0.01s budget.
            import asyncio

            await asyncio.Event().wait()

    class _FakeHub:
        def subscribe(self, _session_id: str) -> _FakeQueue:
            return _FakeQueue()

        def unsubscribe(self, _session_id: str, _queue: _FakeQueue) -> None:
            pass

    user = AuthenticatedUser(
        user_id="u-001",
        username="alice",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )
    gen = events_mod._sse_event_stream(
        _FakeHub(),  # type: ignore[arg-type]
        "sess-1",
        app_state=app_state,  # type: ignore[arg-type]
        user=user,
    )
    saw_revoked = False
    iterations = 0
    async for event in gen:
        iterations += 1
        if event.get("event") == "revoked":
            payload = json.loads(event["data"])
            assert payload["reason"] == "role_demoted"
            saw_revoked = True
            break
        # Safety net: at the configured cadence we should hit revoked
        # within a handful of keepalive ticks (>= 1 keepalive_count
        # required by the loop math). 50 is generous.
        assert iterations < 50
    assert saw_revoked, "SSE stream never emitted the revoked event"
