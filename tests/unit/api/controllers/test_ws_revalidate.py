"""Tests for the WS periodic revalidation task (#1599 §6.1)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.auth.models import AuthenticatedUser, AuthMethod, User
from synthorg.api.controllers.ws import (
    _periodic_revalidate,
    _revocation_reason,
)
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


def _make_auth_user(role: HumanRole = HumanRole.CEO) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="u-001",
        username="alice",
        role=role,
        auth_method=AuthMethod.JWT,
    )


def test_revocation_reason_user_deleted() -> None:
    assert _revocation_reason(None) == "user_deleted"


def test_revocation_reason_role_demoted() -> None:
    user = _make_user(role=HumanRole.SYSTEM)
    assert _revocation_reason(user) == "role_demoted"


def test_revocation_reason_active_user_passes() -> None:
    user = _make_user(role=HumanRole.CEO)
    assert _revocation_reason(user) is None


async def test_periodic_revalidate_closes_on_user_deleted() -> None:
    """When persistence reports the user vanished, the socket closes 4003."""
    socket = _FakeSocket(persisted_user=None)
    user = _make_auth_user()
    await _periodic_revalidate(socket, user, interval_seconds=0)  # type: ignore[arg-type]
    assert socket.closed is True
    assert socket.close_code == 4003
    assert "user_deleted" in (socket.close_reason or "")


async def test_periodic_revalidate_closes_on_role_demoted() -> None:
    """A demoted role triggers a 4003 close."""
    demoted = _make_user(role=HumanRole.SYSTEM)
    socket = _FakeSocket(persisted_user=demoted)
    user = _make_auth_user()
    await _periodic_revalidate(socket, user, interval_seconds=0)  # type: ignore[arg-type]
    assert socket.closed is True
    assert socket.close_code == 4003
    assert "role_demoted" in (socket.close_reason or "")


async def test_periodic_revalidate_tolerates_transient_failure() -> None:
    """Three consecutive transient errors close the socket with 4011."""
    socket = _FakeSocket(persisted_user=_make_user(), raise_on_get=True)
    user = _make_auth_user()
    await _periodic_revalidate(socket, user, interval_seconds=0)  # type: ignore[arg-type]
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
        persistence = type("Pst", (), {"users": users_repo})()
        app_state = type("AS", (), {"persistence": persistence})()
        self.state: dict[str, Any] = {"app_state": app_state}
