"""AuthService.rotate_refresh_token: single-use rotation + reject matrix.

The service owns the rotation policy so the reject matrix is testable
without standing up the full app: a missing / replayed / expired
refresh token or a revoked session is rejected with
``SECURITY_AUTH_REFRESH_REJECTED`` (typed reason) and a
``RefreshTokenInvalidError`` (1005); a valid token rotates *within*
the same session id.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import USER_AUDIENCE, USER_ISSUER
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import User
from synthorg.core.auth.refresh_record import (
    RefreshConsumeOutcome,
    RefreshRecord,
    RefreshRejectReason,
)
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import RefreshTokenInvalidError
from synthorg.observability.events.security import SECURITY_AUTH_REFRESH_REJECTED
from synthorg.persistence.auth_protocol import RefreshTokenRepository
from synthorg.persistence.user_protocol import UserRepository
from tests.unit.api.conftest import _TEST_JWT_SECRET as _SECRET

pytestmark = pytest.mark.unit


def _svc() -> AuthService:
    return AuthService(AuthConfig(jwt_secret=_SECRET))


def _user() -> User:
    now = datetime.now(UTC)
    return User(
        id="user-1",
        username="alice",
        password_hash="$argon2id$dummy",
        role=HumanRole.CEO,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )


def _record() -> RefreshRecord:
    now = datetime.now(UTC)
    return RefreshRecord(
        token_hash="hash-1",
        session_id="sess-original",
        user_id="user-1",
        expires_at=now + timedelta(days=1),
        used=False,
        created_at=now,
    )


def _rejected(reason: RefreshRejectReason) -> RefreshConsumeOutcome:
    return RefreshConsumeOutcome(reject_reason=reason)


async def test_success_rotates_within_same_session() -> None:
    svc = _svc()
    store = AsyncMock(spec=RefreshTokenRepository)
    store.consume.return_value = RefreshConsumeOutcome(record=_record())
    users = AsyncMock(spec=UserRepository)
    users.get.return_value = _user()

    result = await svc.rotate_refresh_token(
        raw_refresh_token="opaque-cookie-value",
        refresh_store=store,
        users=users,
        is_session_revoked=None,
    )

    assert result.user.id == "user-1"
    assert result.session_id == "sess-original"
    assert (
        svc.decode_token(result.token, audience=USER_AUDIENCE, issuer=USER_ISSUER).jti
        == "sess-original"
    )
    # consume() was called with the hashed cookie, not the raw value.
    store.consume.assert_awaited_once()
    assert store.consume.await_args.args[0] == svc.hash_api_key("opaque-cookie-value")


async def test_missing_cookie_rejected_without_consume() -> None:
    svc = _svc()
    store = AsyncMock(spec=RefreshTokenRepository)
    users = AsyncMock(spec=UserRepository)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(RefreshTokenInvalidError),
    ):
        await svc.rotate_refresh_token(
            raw_refresh_token="",
            refresh_store=store,
            users=users,
            is_session_revoked=None,
        )

    store.consume.assert_not_awaited()
    rejected = [
        e
        for e in logs
        if e.get("event") == SECURITY_AUTH_REFRESH_REJECTED
        and e.get("reason") == "cookie_missing"
    ]
    assert len(rejected) == 1


@pytest.mark.parametrize(
    "reason",
    [
        RefreshRejectReason.REPLAY_DETECTED,
        RefreshRejectReason.SESSION_REVOKED,
        RefreshRejectReason.NOT_FOUND_OR_EXPIRED,
    ],
)
async def test_consume_reject_reasons_map_to_1005(
    reason: RefreshRejectReason,
) -> None:
    svc = _svc()
    store = AsyncMock(spec=RefreshTokenRepository)
    store.consume.return_value = _rejected(reason)
    users = AsyncMock(spec=UserRepository)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(RefreshTokenInvalidError),
    ):
        await svc.rotate_refresh_token(
            raw_refresh_token="opaque",
            refresh_store=store,
            users=users,
            is_session_revoked=None,
        )

    users.get.assert_not_awaited()
    rejected = [
        e
        for e in logs
        if e.get("event") == SECURITY_AUTH_REFRESH_REJECTED
        and e.get("reason") == reason.value
    ]
    assert len(rejected) == 1


async def test_user_deleted_after_consume_rejected() -> None:
    svc = _svc()
    store = AsyncMock(spec=RefreshTokenRepository)
    store.consume.return_value = RefreshConsumeOutcome(record=_record())
    users = AsyncMock(spec=UserRepository)
    users.get.return_value = None

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(RefreshTokenInvalidError),
    ):
        await svc.rotate_refresh_token(
            raw_refresh_token="opaque",
            refresh_store=store,
            users=users,
            is_session_revoked=None,
        )

    rejected = [
        e
        for e in logs
        if e.get("event") == SECURITY_AUTH_REFRESH_REJECTED
        and e.get("reason") == "user_not_found_after_consume"
    ]
    assert len(rejected) == 1
