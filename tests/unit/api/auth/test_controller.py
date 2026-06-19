"""Tests for AuthController endpoints."""

from typing import cast
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from typeguard import suppress_type_checks

from synthorg.api.api_core_state import (
    ApiCoreStateSlice,
    auth_service_of,
    ticket_store_of,
)
from synthorg.api.state import AppState
from synthorg.core.auth.roles import HumanRole
from synthorg.persistence.state import persistence_of
from tests._shared import LoopAsyncClient, as_uuid
from tests.unit.api.conftest import _TEST_JWT_SECRET, make_auth_headers
from tests.unit.api.fakes import FakePersistenceBackend


@pytest.fixture
async def bare_client(async_test_client: LoopAsyncClient) -> LoopAsyncClient:
    """Test client with no default Authorization header."""
    async_test_client.headers.pop("authorization", None)
    return async_test_client


def _extract_auth_cookies(response: httpx.Response) -> dict[str, str]:
    """Extract session and CSRF cookies from Set-Cookie headers.

    Returns a dict with ``session`` and ``csrf_token`` keys.
    """
    result: dict[str, str] = {}
    for _k, v in response.headers.multi_items():
        if _k != "set-cookie":
            continue
        if v.startswith("session="):
            result["session"] = v.split("session=")[1].split(";")[0]
        elif v.startswith("csrf_token="):
            result["csrf_token"] = v.split("csrf_token=")[1].split(";")[0]
    assert "session" in result, "missing session cookie"
    assert "csrf_token" in result, "missing csrf_token cookie"
    return result


@pytest.mark.unit
class TestSetup:
    async def test_setup_creates_admin(self, bare_client: LoopAsyncClient) -> None:
        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "newadmin",
                "password": "super-secure-password-12",
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert "token" not in data  # JWT is in HttpOnly cookie, not body
        assert data["must_change_password"] is False
        assert data["expires_in"] > 0
        # Verify session cookie is set via Set-Cookie header
        set_cookie = response.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "httponly" in set_cookie.lower()

    async def test_setup_409_when_users_exist(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        # Re-seed a user so the check fails
        from datetime import UTC, datetime

        from synthorg.api.auth.service import AuthService
        from synthorg.core.auth.models import User
        from synthorg.core.auth.roles import HumanRole

        app_state = bare_client.app.state["app_state"]
        svc: AuthService = auth_service_of(app_state)
        now = datetime.now(UTC)
        user = User(
            id=str(as_uuid("existing-ceo")),
            username="existing",
            password_hash=await svc.hash_password("test-password-12chars"),
            role=HumanRole.CEO,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users[
            user.id
        ] = user

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "admin2",
                "password": "super-secure-password-12",
            },
        )
        assert response.status_code == 409

    async def test_setup_short_password_rejected(
        self, bare_client: LoopAsyncClient
    ) -> None:
        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={"username": "admin", "password": "short"},
        )
        assert response.status_code == 400

    async def test_setup_race_single_ceo_returns_409(
        self,
        bare_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A losing racer on the single-CEO guard sees 'already completed'.

        ``count_by_role`` is monkeypatched to return 0 for any role,
        simulating the racing window where the incumbent CEO is not yet
        visible to the pre-check. ``save`` then trips the fake's single-CEO
        guard (which mirrors the real ``idx_single_ceo`` partial-unique index
        by raising ``ConstraintViolationError`` with
        ``constraint=IDX_SINGLE_CEO``). The guard maps it to the uniform
        setup-complete 409 rather than leaking the persistence token.
        """
        from datetime import UTC, datetime

        from synthorg.core.auth.models import User

        app_state = bare_client.app.state["app_state"]
        backend = cast(FakePersistenceBackend, persistence_of(app_state))
        users_repo = backend._users
        users_repo._users.clear()
        now = datetime.now(UTC)
        users_repo.seed(
            User(
                id=str(as_uuid("incumbent-ceo")),
                username="incumbent-ceo",
                password_hash="x",
                role=HumanRole.CEO,
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
        )

        async def _no_ceos(role: HumanRole) -> int:
            return 0

        monkeypatch.setattr(users_repo, "count_by_role", _no_ceos)

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={"username": "newadmin", "password": "super-secure-password-12"},
        )
        assert response.status_code == 409
        assert "Setup already completed" in response.text

    async def test_setup_race_duplicate_username_returns_409(
        self, bare_client: LoopAsyncClient
    ) -> None:
        """A losing racer on the username-unique guard sees 'already completed'.

        An OBSERVER (not a CEO) is seeded under the target username, so the
        CEO pre-check passes (no CEO yet) but ``save`` raises the
        username-unique constraint, which the guard maps to the uniform
        setup-complete 409 via the typed ``DuplicateUsernameError``.
        """
        from datetime import UTC, datetime

        from synthorg.core.auth.models import User

        app_state = bare_client.app.state["app_state"]
        backend = cast(FakePersistenceBackend, persistence_of(app_state))
        backend._users._users.clear()
        now = datetime.now(UTC)
        backend._users.seed(
            User(
                id=str(as_uuid("taken-observer")),
                username="taken",
                password_hash="x",
                role=HumanRole.OBSERVER,
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
        )

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={"username": "taken", "password": "super-secure-password-12"},
        )
        assert response.status_code == 409
        assert "Setup already completed" in response.text

    async def test_setup_race_last_ceo_constraint_returns_409(
        self,
        bare_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every recognised user-constraint token maps to 'already completed'.

        The last-CEO trigger cannot fire on a bootstrap INSERT, but the guard
        must treat every recognised user-constraint token uniformly: ``save``
        raising ``LAST_CEO_TRIGGER`` is mapped to the setup-complete 409 by
        ``raise_for_user_constraint`` rather than surfacing the typed
        ``LastCeoConstraintError`` default message.
        """
        from synthorg.core.constraint_tokens import LAST_CEO_TRIGGER
        from synthorg.core.persistence_errors import ConstraintViolationError

        app_state = bare_client.app.state["app_state"]
        backend = cast(FakePersistenceBackend, persistence_of(app_state))
        users_repo = backend._users
        users_repo._users.clear()

        async def _raise_last_ceo(entity: object) -> None:
            msg = "last ceo"
            raise ConstraintViolationError(msg, constraint=LAST_CEO_TRIGGER)

        monkeypatch.setattr(users_repo, "save", _raise_last_ceo)

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={"username": "newadmin", "password": "super-secure-password-12"},
        )
        assert response.status_code == 409
        assert "Setup already completed" in response.text

    async def test_setup_unrelated_constraint_not_masked(
        self,
        bare_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unrelated constraint propagates, not masked as setup-complete.

        The race guard only remaps recognised user-constraint conflicts; any
        other constraint is re-raised as the original ``ConstraintViolationError``
        and surfaces via the persistence-integrity handler (400) instead of a
        misleading 'already completed' response.
        """
        from synthorg.core.persistence_errors import ConstraintViolationError

        app_state = bare_client.app.state["app_state"]
        backend = cast(FakePersistenceBackend, persistence_of(app_state))
        users_repo = backend._users
        users_repo._users.clear()

        async def _raise_unrelated(entity: object) -> None:
            msg = "unrelated"
            raise ConstraintViolationError(msg, constraint="SOME_OTHER_CONSTRAINT")

        monkeypatch.setattr(users_repo, "save", _raise_unrelated)

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={"username": "newadmin", "password": "super-secure-password-12"},
        )
        assert response.status_code == 400
        assert "Setup already completed" not in response.text


@pytest.mark.unit
class TestLogin:
    async def test_login_valid_credentials(self, bare_client: LoopAsyncClient) -> None:
        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()

        await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "loginuser",
                "password": "super-secure-password-12",
            },
        )

        response = await bare_client.post(
            "/api/v1/auth/login",
            json={
                "username": "loginuser",
                "password": "super-secure-password-12",
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "token" not in data  # JWT is in HttpOnly cookie
        assert data["expires_in"] > 0
        set_cookie = response.headers.get("set-cookie", "")
        assert "session=" in set_cookie

    async def test_login_wrong_password(self, bare_client: LoopAsyncClient) -> None:
        response = await bare_client.post(
            "/api/v1/auth/login",
            json={
                "username": "test-ceo",
                "password": "wrong-password-12345",
            },
        )
        assert response.status_code == 401

    async def test_login_nonexistent_user(self, bare_client: LoopAsyncClient) -> None:
        response = await bare_client.post(
            "/api/v1/auth/login",
            json={
                "username": "nonexistent",
                "password": "any-password-12345",
            },
        )
        assert response.status_code == 401


@pytest.mark.unit
class TestChangePassword:
    async def test_change_password_success(self, bare_client: LoopAsyncClient) -> None:
        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()

        setup_resp = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "changepw",
                "password": "old-password-12chars",
            },
        )
        # Extract session JWT and CSRF token from Set-Cookie headers
        cookies = _extract_auth_cookies(setup_resp)

        response = await bare_client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "old-password-12chars",
                "new_password": "new-password-12chars",
            },
            headers={
                "Cookie": (
                    f"session={cookies['session']}; csrf_token={cookies['csrf_token']}"
                ),
                "X-CSRF-Token": cookies["csrf_token"],
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["must_change_password"] is False

    async def test_change_password_wrong_current(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "wrong-current-pw-12",
                "new_password": "new-password-12chars",
            },
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 401

    async def test_change_password_requires_auth(
        self, bare_client: LoopAsyncClient
    ) -> None:
        response = await bare_client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "old-password-12chars",
                "new_password": "new-password-12chars",
            },
        )
        assert response.status_code == 401

    async def test_change_password_short_new_password(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()

        setup_resp = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "shortpw",
                "password": "old-password-12chars",
            },
        )
        cookies = _extract_auth_cookies(setup_resp)

        response = await bare_client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "old-password-12chars",
                "new_password": "short",
            },
            headers={
                "Cookie": (
                    f"session={cookies['session']}; csrf_token={cookies['csrf_token']}"
                ),
                "X-CSRF-Token": cookies["csrf_token"],
            },
        )
        assert response.status_code == 400


@pytest.mark.unit
class TestMe:
    async def test_me_returns_user_info(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get(
            "/api/v1/auth/me",
            headers=make_auth_headers("ceo"),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["username"] == "test-ceo"
        assert data["role"] == "ceo"
        assert data["must_change_password"] is False

    async def test_me_requires_auth(self, bare_client: LoopAsyncClient) -> None:
        response = await bare_client.get("/api/v1/auth/me")
        assert response.status_code == 401


@pytest.mark.unit
class TestRequirePasswordChanged:
    def test_blocks_user_with_must_change_password(self) -> None:
        """Guard raises PermissionDeniedException for flagged users."""
        from unittest.mock import MagicMock

        from litestar.exceptions import PermissionDeniedException

        from synthorg.api.auth.controller_helpers import require_password_changed
        from synthorg.core.auth.models import AuthenticatedUser, AuthMethod

        user = AuthenticatedUser(
            user_id="u1",
            username="admin",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
            must_change_password=True,
        )
        connection = MagicMock()
        connection.scope = {"user": user}
        connection.url.path = "/api/v1/healthz"

        with pytest.raises(PermissionDeniedException):
            require_password_changed(connection, None)

    def test_allows_user_without_flag(self) -> None:
        """Guard passes when must_change_password is False."""
        from unittest.mock import MagicMock

        from synthorg.api.auth.controller_helpers import require_password_changed
        from synthorg.core.auth.models import AuthenticatedUser, AuthMethod

        user = AuthenticatedUser(
            user_id="u1",
            username="admin",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
            must_change_password=False,
        )
        connection = MagicMock()
        connection.scope = {"user": user}
        connection.url.path = "/api/v1/healthz"

        # Should not raise
        require_password_changed(connection, None)

    def test_allows_when_no_user_in_scope(self) -> None:
        """Guard passes when no user is in scope (pre-auth)."""
        from unittest.mock import MagicMock

        from synthorg.api.auth.controller_helpers import require_password_changed

        connection = MagicMock()
        connection.scope = {}
        connection.url.path = "/api/v1/healthz"

        # Should not raise
        require_password_changed(connection, None)

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("/api/v1/auth/change-password", id="change-password"),
            pytest.param("/api/v1/auth/me", id="me"),
        ],
    )
    def test_exempts_paths_for_must_change_password_users(
        self,
        path: str,
    ) -> None:
        """Guard allows must_change_password users on exempt paths."""
        from unittest.mock import MagicMock

        from synthorg.api.auth.controller_helpers import require_password_changed
        from synthorg.core.auth.models import AuthenticatedUser, AuthMethod

        user = AuthenticatedUser(
            user_id="u1",
            username="admin",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
            must_change_password=True,
        )
        connection = MagicMock()
        connection.scope = {"user": user}
        connection.url.path = path

        # Should not raise -- exempt path
        require_password_changed(connection, None)

    def test_rejects_unknown_user_type(self) -> None:
        """Guard raises PermissionDeniedException for non-AuthenticatedUser."""
        from unittest.mock import MagicMock

        from litestar.exceptions import PermissionDeniedException

        from synthorg.api.auth.controller_helpers import require_password_changed

        connection = MagicMock()
        connection.scope = {"user": "not-an-auth-user"}
        connection.url.path = "/api/v1/healthz"

        with pytest.raises(PermissionDeniedException):
            require_password_changed(connection, None)


@pytest.mark.unit
class TestWsTicket:
    async def test_ws_ticket_returns_ticket_and_expires_in(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        response = await async_test_client.post("/api/v1/auth/ws-ticket")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "ticket" in data
        assert isinstance(data["ticket"], str)
        assert len(data["ticket"]) > 0
        assert data["expires_in"] == 30

    async def test_ws_ticket_requires_auth(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        response = await bare_client.post("/api/v1/auth/ws-ticket")
        assert response.status_code == 401

    async def test_ws_ticket_with_observer_role(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """All roles should be able to get a WS ticket."""
        response = await async_test_client.post(
            "/api/v1/auth/ws-ticket",
            headers=make_auth_headers("observer"),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "ticket" in data

    async def test_ws_ticket_is_consumable(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The returned ticket can be consumed by the ticket store."""
        response = await async_test_client.post("/api/v1/auth/ws-ticket")
        data = response.json()["data"]
        ticket = data["ticket"]

        app_state = async_test_client.app.state["app_state"]
        user = await ticket_store_of(app_state).validate_and_consume(ticket)
        assert user is not None
        assert user.auth_method.value == "ws_ticket"

        # Single-use: second consume fails
        assert await ticket_store_of(app_state).validate_and_consume(ticket) is None


@pytest.mark.unit
class TestSystemUserBlocking:
    """Verify the system user cannot log in or change its password."""

    async def test_login_rejects_system_user(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        """System user login returns 401 (same as invalid credentials)."""
        from datetime import UTC, datetime

        from synthorg.api.auth.system_user import (
            SYSTEM_USER_ID,
            SYSTEM_USERNAME,
        )
        from synthorg.core.auth.models import User

        # Seed a system user via the public save() API
        app_state = bare_client.app.state["app_state"]
        now = datetime.now(UTC)
        svc = auth_service_of(app_state)
        system_user = User(
            id=SYSTEM_USER_ID,
            username=SYSTEM_USERNAME,
            password_hash=await svc.hash_password("irrelevant-password-12"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        # Use internal dict because save() is async and this is a sync test
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users[
            SYSTEM_USER_ID
        ] = system_user

        response = await bare_client.post(
            "/api/v1/auth/login",
            json={
                "username": "system",
                "password": "irrelevant-password-12",
            },
        )
        assert response.status_code == 401

    async def test_change_password_rejects_system_user(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        """System user cannot change its password (403)."""
        from datetime import UTC, datetime, timedelta

        import jwt as pyjwt

        from synthorg.api.auth.system_user import (
            SYSTEM_USER_ID,
            SYSTEM_USERNAME,
        )
        from synthorg.core.auth.models import User

        # Explicitly seed the system user so the test is self-contained
        app_state = bare_client.app.state["app_state"]
        now = datetime.now(UTC)
        svc = auth_service_of(app_state)
        system_user = User(
            id=SYSTEM_USER_ID,
            username=SYSTEM_USERNAME,
            password_hash=await svc.hash_password("irrelevant-password-12"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users[
            SYSTEM_USER_ID
        ] = system_user

        # Build a CLI-style JWT with iss + aud (required by middleware)
        token = pyjwt.encode(
            {
                "sub": SYSTEM_USER_ID,
                "iss": "synthorg-cli",
                "aud": "synthorg-backend",
                "jti": "sys-change-pwd",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _TEST_JWT_SECRET,
            algorithm="HS256",
        )

        response = await bare_client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "any-password-12chars",
                "new_password": "new-password-12chars",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    async def test_setup_succeeds_with_system_user_present(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        """Setup endpoint returns 201 when only the system user exists."""
        from datetime import UTC, datetime

        from synthorg.api.auth.system_user import (
            SYSTEM_USER_ID,
            SYSTEM_USERNAME,
        )
        from synthorg.core.auth.models import User

        app_state = bare_client.app.state["app_state"]
        # Clear all users, then add only the system user
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()
        now = datetime.now(UTC)
        system_user = User(
            id=SYSTEM_USER_ID,
            username=SYSTEM_USERNAME,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$fake$hash",
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users[
            SYSTEM_USER_ID
        ] = system_user

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "newadmin",
                "password": "super-secure-password-12",
            },
        )
        assert response.status_code == 201

    async def test_setup_rejects_reserved_system_username(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        """Setup rejects the reserved 'system' username with 409."""
        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "system",
                "password": "super-secure-password-12",
            },
        )
        assert response.status_code == 409

    async def test_setup_succeeds_with_non_ceo_human_users_present(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        """Setup succeeds when only non-CEO human users exist."""
        from datetime import UTC, datetime

        from synthorg.core.auth.models import User

        app_state = bare_client.app.state["app_state"]
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users.clear()
        now = datetime.now(UTC)
        observer = User(
            id="observer-001",
            username="watcher",
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$fake$hash",
            role=HumanRole.OBSERVER,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        cast(FakePersistenceBackend, persistence_of(app_state))._users._users[
            "observer-001"
        ] = observer

        response = await bare_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "firstadmin",
                "password": "super-secure-password-12",
            },
        )
        assert response.status_code == 201


@pytest.mark.unit
class TestLogoutIdempotency:
    """The ``POST /auth/logout`` endpoint must be idempotent.

    Regardless of whether the caller is authenticated -- or whether
    the server-side session store call succeeds -- logout must always
    return 204 and emit clear-cookie + ``Clear-Site-Data`` headers so
    clients can recover from stale cookie state.
    """

    def _assert_clear_cookies(self, response: httpx.Response) -> None:
        """Assert each clear-cookie header carries ``Max-Age=0``.

        Checking ``max-age=0`` on the concatenated string would still
        pass if one cookie regressed while another kept the attribute.
        Match each expected cookie header individually so a regression
        on any single cookie is caught.
        """
        set_cookies = [
            v for k, v in response.headers.multi_items() if k == "set-cookie"
        ]
        for cookie_name in ("session", "csrf_token", "refresh_token"):
            matching = [
                c for c in set_cookies if c.lower().startswith(f"{cookie_name}=")
            ]
            assert matching, f"missing Set-Cookie for {cookie_name}"
            assert "max-age=0" in matching[0].lower(), (
                f"{cookie_name} cookie lacks Max-Age=0: {matching[0]}"
            )
        assert response.headers.get("Clear-Site-Data") == '"cookies"'

    async def test_logout_without_auth_returns_204_with_clear_cookies(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        # No Authorization header, no session cookie -- the idempotent
        # logout still has to work so clients with stale cookies can
        # clear them.
        response = await bare_client.post("/api/v1/auth/logout")
        assert response.status_code == 204
        self._assert_clear_cookies(response)

    async def test_logout_with_invalid_bearer_still_returns_204(
        self,
        bare_client: LoopAsyncClient,
    ) -> None:
        response = await bare_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert response.status_code == 204
        self._assert_clear_cookies(response)

    @staticmethod
    def _install_spy_session_store(
        app_state: AppState,
        revoke_spy: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Attach a minimal mock session store exposing ``revoke``.

        The ``bare_client`` shared fixture runs against a persistence
        backend that doesn't expose a raw DB, so the session store is
        unwired by default.  Wire a namespace whose ``revoke`` is the
        spy into the api-core slice; ``setitem`` restores the original
        slice at test teardown.
        """
        from types import SimpleNamespace

        spied = app_state.slice(ApiCoreStateSlice).model_copy(
            update={"session_store": SimpleNamespace(revoke=revoke_spy)},
        )
        monkeypatch.setitem(app_state._slices, ApiCoreStateSlice, spied)  # type: ignore[misc]  # mypy cannot infer the type[]-keyed dict's K for setitem

    async def test_logout_with_valid_auth_returns_204_and_revokes_session(
        self,
        bare_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app_state = bare_client.app.state["app_state"]
        # Spy on revoke so we can assert the server-side record
        # was invalidated.  Because ``/auth/logout`` is in
        # ``auth.exclude_paths`` the auth middleware does not run
        # on this route -- the handler must therefore extract the
        # JTI itself and call revoke directly.
        revoke_spy = AsyncMock(return_value=None)
        self._install_spy_session_store(app_state, revoke_spy, monkeypatch)

        headers = make_auth_headers(role=HumanRole.CEO)
        token = headers["Authorization"].removeprefix("Bearer ")
        # Enforce iss/aud verification on the test-side decode so a
        # regression in ``create_token``/``make_auth_headers`` that
        # drops or mis-sets the hardened claims would surface here
        # rather than silently let the test pass while production
        # middleware rejects the same token.
        from synthorg.api.auth.system_user import (
            USER_AUDIENCE,
            USER_ISSUER,
        )

        expected_jti = jwt.decode(
            token,
            _TEST_JWT_SECRET,
            algorithms=["HS256"],
            issuer=USER_ISSUER,
            audience=USER_AUDIENCE,
        )["jti"]

        # The spy session store is a minimal ``SimpleNamespace(revoke=...)`` that
        # cannot satisfy the full ``SessionRepository`` protocol (it declares a
        # ``_revoked`` data attribute plus the whole repository surface). This test
        # verifies the revoke call + 204 contract, not store type conformance, so
        # the server-side runtime check is suppressed for the logout request.
        with suppress_type_checks():
            response = await bare_client.post(
                "/api/v1/auth/logout",
                headers=headers,
            )
        assert response.status_code == 204
        self._assert_clear_cookies(response)
        revoke_spy.assert_awaited_once_with(expected_jti)

    async def test_logout_still_204_when_session_store_revoke_fails(
        self,
        bare_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app_state = bare_client.app.state["app_state"]
        # Spy that raises -- proves the handler both *invokes*
        # revoke AND correctly catches the failure while still
        # returning 204 with clear cookies (idempotent contract).
        revoke_spy = AsyncMock(
            side_effect=RuntimeError("simulated session store failure"),
        )
        self._install_spy_session_store(app_state, revoke_spy, monkeypatch)

        # See the sibling test: the spy session store cannot satisfy the full
        # ``SessionRepository`` protocol, so the server-side runtime check is
        # suppressed for the logout request (the test asserts the 204 + revoke
        # contract, not store type conformance).
        with suppress_type_checks():
            response = await bare_client.post(
                "/api/v1/auth/logout",
                headers=make_auth_headers(role=HumanRole.CEO),
            )
        # Idempotent contract: the client is trying to recover from
        # stale state, so a transient revoke failure must not mask
        # the cookie-clear with a 500.
        assert response.status_code == 204
        self._assert_clear_cookies(response)
        revoke_spy.assert_awaited_once()
