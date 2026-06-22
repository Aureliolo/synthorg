"""Tests for the WS periodic revalidation task."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from typeguard import suppress_type_checks

from synthorg.api.controllers.ws_revalidation import (
    _periodic_revalidate,
    _revocation_reason,
)
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod, User
from synthorg.core.auth.roles import HumanRole
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import persistence_of
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


async def _run_revalidate(socket: _FakeSocket, user: AuthenticatedUser) -> None:
    """Drive ``_periodic_revalidate`` with a behavioural ``_FakeSocket``.

    ``socket`` is a close-capturing stand-in for a concrete ``WebSocket``;
    the runtime check is suppressed at the same boundary as the static
    ``# type: ignore[arg-type]`` these calls carried, because the tests
    verify close behaviour, not socket type conformance.
    """
    with suppress_type_checks():
        await _periodic_revalidate(socket, user, interval_seconds=0)  # type: ignore[arg-type]  # behavioural socket stub, not a real WebSocket


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


def _make_auth_user(role: HumanRole = HumanRole.CEO) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="u-001",
        username="alice",
        role=role,
        auth_method=AuthMethod.JWT,
    )


def _make_app_state(
    *,
    has_session_store: bool = False,
    is_revoked: bool = False,
) -> AppState:
    """Lightweight ``app_state`` stub for ``_revocation_reason``."""
    session_store = (
        type("Ss", (), {"is_revoked": lambda _self, _jti: is_revoked})()
        if has_session_store
        else None
    )
    return make_app_state(session_store=session_store)


async def test_revocation_reason_user_deleted() -> None:
    auth = _make_auth_user()
    assert await _revocation_reason(None, auth, _make_app_state()) == (
        "user_deleted",
        True,
    )


async def test_revocation_reason_role_demoted() -> None:
    user = _make_user(role=HumanRole.SYSTEM)
    auth = _make_auth_user()
    assert await _revocation_reason(user, auth, _make_app_state()) == (
        "role_demoted",
        True,
    )


async def test_revocation_reason_active_user_passes() -> None:
    user = _make_user(role=HumanRole.CEO)
    auth = _make_auth_user()
    assert await _revocation_reason(user, auth, _make_app_state()) == (None, True)


async def test_revocation_reason_session_revoked_kicks_connection() -> None:
    """A revoked JTI must surface as ``session_revoked`` even when the
    user record is otherwise authorised."""
    user = _make_user(role=HumanRole.CEO)
    auth = _make_auth_user().model_copy(update={"session_id": "jti-123"})
    app_state = _make_app_state(has_session_store=True, is_revoked=True)
    assert await _revocation_reason(user, auth, app_state) == ("session_revoked", True)


async def test_revocation_reason_no_session_id_skips_session_check() -> None:
    """Auth methods without a JTI (e.g. API key) bypass the session
    check; an active user passes."""
    user = _make_user(role=HumanRole.CEO)
    auth = _make_auth_user()  # session_id defaults to None
    app_state = _make_app_state(has_session_store=True, is_revoked=True)
    assert await _revocation_reason(user, auth, app_state) == (None, True)


async def test_periodic_revalidate_closes_on_user_deleted() -> None:
    """When persistence reports the user vanished, the socket closes 4003."""
    socket = _FakeSocket(persisted_user=None)
    user = _make_auth_user()
    await _run_revalidate(socket, user)
    assert socket.closed is True
    assert socket.close_code == 4003
    assert "user_deleted" in (socket.close_reason or "")


async def test_periodic_revalidate_closes_on_role_demoted() -> None:
    """A demoted role triggers a 4003 close."""
    demoted = _make_user(role=HumanRole.SYSTEM)
    socket = _FakeSocket(persisted_user=demoted)
    user = _make_auth_user()
    await _run_revalidate(socket, user)
    assert socket.closed is True
    assert socket.close_code == 4003
    assert "role_demoted" in (socket.close_reason or "")


async def test_periodic_revalidate_tolerates_transient_failure() -> None:
    """Three consecutive transient errors close the socket with 4011.

    The fake app state caps ``auth_revalidate_max_failures`` at 3, so
    the third take() returns False and the limiter triggers the close
    with code 4011 (server error / revalidation backend unavailable).
    """
    socket = _FakeSocket(persisted_user=_make_user(), raise_on_get=True)
    user = _make_auth_user()
    await _run_revalidate(socket, user)
    assert socket.closed is True
    assert socket.close_code == 4011


async def test_periodic_revalidate_failure_window_does_not_reset_on_success() -> None:
    """Sliding-window tracking does not reset on intervening successes.

    Three successes in a row do NOT clear earlier failures: once the
    failure budget within the configured window is exhausted, the
    socket closes regardless of any successful tick that happened
    between the failure cluster and the saturating failure.
    """
    socket = _FakeSocket(persisted_user=_make_user())
    cast(
        AsyncMock, persistence_of(socket.app.state["app_state"]).users.get
    ).side_effect = [
        RuntimeError("blip 1"),
        _make_user(),  # success
        RuntimeError("blip 2"),
        _make_user(),  # success
        RuntimeError("blip 3"),  # saturates the 3-slot window -> close
    ]
    user = _make_auth_user()
    await _run_revalidate(socket, user)
    assert socket.closed is True
    assert socket.close_code == 4011


# ── Fakes ────────────────────────────────────────────────────────


class _FakeSocket:
    """Stand-in for ``WebSocket`` capturing close calls."""

    def __init__(
        self,
        *,
        persisted_user: User | None,
        raise_on_get: bool = False,
    ) -> None:
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.client = ("127.0.0.1", 1234)
        self.app = _FakeApp(persisted_user, raise_on_get=raise_on_get)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


class _FakeApp:
    def __init__(
        self,
        persisted_user: User | None,
        *,
        raise_on_get: bool,
    ) -> None:
        users_repo = AsyncMock()
        if raise_on_get:
            users_repo.get.side_effect = RuntimeError("transient db blip")
        else:
            users_repo.get.return_value = persisted_user
        persistence = mock_of[PersistenceBackend](users=users_repo)
        app_state = make_app_state(persistence=persistence)
        # Tight revalidation-window bounds so the transient-failure
        # regression test can saturate the window in a few iterations.
        app_state.ws_auth_limits.set_auth_revalidate_window_seconds(60)
        app_state.ws_auth_limits.set_auth_revalidate_max_failures(3)
        self.state: dict[str, AppState] = {"app_state": app_state}
