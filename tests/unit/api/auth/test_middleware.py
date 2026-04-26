"""Tests for ApiAuthMiddleware."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.middleware import create_auth_middleware_class
from synthorg.api.auth.models import ApiKey, User
from synthorg.api.auth.service import AuthService
from synthorg.api.config import ApiConfig
from synthorg.api.guards import HumanRole
from synthorg.config.schema import RootConfig
from tests.unit.api.conftest import _TEST_JWT_SECRET as _SECRET
from tests.unit.api.conftest import FakePersistenceBackend


def _pwd_sig(password_hash: str) -> str:
    """Mirror AuthService.create_token's pwd_sig derivation."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


class _FakeState:
    """Reusable fake app state for middleware tests."""

    def __init__(
        self,
        *,
        auth_service: AuthService,
        persistence: FakePersistenceBackend,
        has_session_store: bool = False,
        session_store: object | None = None,
        config: RootConfig | None = None,
    ) -> None:
        self.auth_service = auth_service
        self.persistence = persistence
        self.has_session_store = has_session_store
        self.session_store = session_store
        self.config = config


def _make_auth_service() -> AuthService:
    return AuthService(AuthConfig(jwt_secret=_SECRET))


def _make_user(svc: AuthService) -> User:
    now = datetime.now(UTC)
    return User(
        id="mw-user-001",
        username="mw-admin",
        password_hash=svc.hash_password("test-password-12chars"),
        role=HumanRole.CEO,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )


def _build_app(
    *,
    auth_service: AuthService,
    persistence: FakePersistenceBackend,
    exclude_paths: tuple[str, ...] = (),
) -> Litestar:
    """Build a minimal Litestar app with auth middleware."""
    auth_config = AuthConfig(
        jwt_secret=_SECRET,
        exclude_paths=exclude_paths,
    )

    @get("/protected")
    async def protected_route() -> dict[str, str]:
        return {"status": "ok"}

    @get("/public")
    async def public_route() -> dict[str, str]:
        return {"status": "public"}

    middleware_cls = create_auth_middleware_class(auth_config)

    app = Litestar(
        route_handlers=[protected_route, public_route],
        middleware=[middleware_cls],
    )
    app.state["app_state"] = _FakeState(
        auth_service=auth_service,
        persistence=persistence,
    )
    return app


@pytest.mark.unit
class TestAuthMiddlewareJWT:
    async def test_valid_jwt_authenticates(self) -> None:
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        app = _build_app(auth_service=svc, persistence=persistence)
        token, _, _ = svc.create_token(user)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_missing_header_returns_401(self) -> None:
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get("/protected")
            assert resp.status_code == 401

    async def test_invalid_scheme_returns_401(self) -> None:
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
            )
            assert resp.status_code == 401

    async def test_invalid_jwt_returns_401(self) -> None:
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer bad.jwt.token"},
            )
            assert resp.status_code == 401

    async def test_jwt_after_password_change_returns_401(self) -> None:
        """Token issued before password change is rejected via pwd_sig."""
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        token, _, _ = svc.create_token(user)

        # Change password -- new hash means different pwd_sig
        updated_user = User(
            id=user.id,
            username=user.username,
            password_hash=svc.hash_password("new-password-12chars"),
            role=user.role,
            must_change_password=False,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        await persistence.users.save(updated_user)

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

    async def test_jwt_auth_with_unconfigured_secret_returns_401(
        self,
    ) -> None:
        """JWT auth degrades to 401 when secret is unconfigured."""
        empty_svc = AuthService(AuthConfig())
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=empty_svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer some.jwt.token"},
            )
            assert resp.status_code == 401

    async def test_jwt_for_deleted_user_returns_401(self) -> None:
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        # Don't save user -- simulate deleted user
        token, _, _ = svc.create_token(user)
        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401


@pytest.mark.unit
class TestAuthMiddlewareRevocation:
    async def test_revoked_session_returns_401(self) -> None:
        """JWT with a revoked jti is rejected by the middleware."""
        from unittest.mock import MagicMock

        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        token, _, session_id = svc.create_token(user)

        mock_store = MagicMock()
        mock_store.is_revoked.return_value = True

        auth_config = AuthConfig(jwt_secret=_SECRET)

        @get("/protected")
        async def protected_route() -> dict[str, str]:
            return {"status": "ok"}

        middleware_cls = create_auth_middleware_class(auth_config)

        class _RevokedState:
            def __init__(self) -> None:
                self.auth_service = svc
                self.persistence = persistence
                self.has_session_store = True
                self.session_store = mock_store

        app = Litestar(
            route_handlers=[protected_route],
            middleware=[middleware_cls],
        )
        app.state["app_state"] = _RevokedState()

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

        mock_store.is_revoked.assert_called_once_with(session_id)


@pytest.mark.unit
class TestAuthMiddlewareApiKey:
    async def test_valid_api_key_authenticates(self) -> None:
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        raw_key = AuthService.generate_api_key()
        key_hash = svc.hash_api_key(raw_key)
        now = datetime.now(UTC)
        api_key = ApiKey(
            id="key-001",
            key_hash=key_hash,
            name="test-key",
            role=HumanRole.CEO,
            user_id=user.id,
            created_at=now,
        )
        await persistence.api_keys.save(api_key)

        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert resp.status_code == 200

    async def test_revoked_api_key_returns_401(self) -> None:
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        raw_key = AuthService.generate_api_key()
        key_hash = svc.hash_api_key(raw_key)
        now = datetime.now(UTC)
        api_key = ApiKey(
            id="key-002",
            key_hash=key_hash,
            name="revoked-key",
            role=HumanRole.CEO,
            user_id=user.id,
            created_at=now,
            revoked=True,
        )
        await persistence.api_keys.save(api_key)

        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert resp.status_code == 401

    async def test_expired_api_key_returns_401(self) -> None:
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        raw_key = AuthService.generate_api_key()
        key_hash = svc.hash_api_key(raw_key)
        now = datetime.now(UTC)
        api_key = ApiKey(
            id="key-003",
            key_hash=key_hash,
            name="expired-key",
            role=HumanRole.CEO,
            user_id=user.id,
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        await persistence.api_keys.save(api_key)

        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert resp.status_code == 401


@pytest.mark.unit
class TestAuthMiddlewareApiKeyEdgeCases:
    async def test_api_key_with_deleted_owner_returns_401(self) -> None:
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        # Create a manager (not CEO) so deletion doesn't violate invariants.
        # The CEO from _make_user stays to satisfy the CEO-minimum trigger.
        ceo = _make_user(svc)
        await persistence.users.save(ceo)
        now = datetime.now(UTC)
        user = User(
            id="mw-deletable",
            username="deletable",
            password_hash=ceo.password_hash,
            role=HumanRole.MANAGER,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(user)

        raw_key = AuthService.generate_api_key()
        key_hash = svc.hash_api_key(raw_key)
        now = datetime.now(UTC)
        api_key = ApiKey(
            id="key-orphan",
            key_hash=key_hash,
            name="orphaned-key",
            role=HumanRole.MANAGER,
            user_id=user.id,
            created_at=now,
        )
        await persistence.api_keys.save(api_key)
        await persistence.users.delete(user.id)

        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert resp.status_code == 401

    async def test_unknown_api_key_returns_401(self) -> None:
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=svc, persistence=persistence)

        # Send a token without dots (API key path) that is not registered
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer unknownkey123456"},
            )
            assert resp.status_code == 401

    async def test_api_key_auth_with_unconfigured_secret_returns_401(
        self,
    ) -> None:
        empty_svc = AuthService(AuthConfig())
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=empty_svc, persistence=persistence)

        # hash_api_key raises SecretNotConfiguredError; middleware handles it
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer sometokenwithnodots"},
            )
            assert resp.status_code == 401


@pytest.mark.unit
class TestExtractBearerToken:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            pytest.param("Bearer mytoken123", "mytoken123", id="valid"),
            pytest.param("bearer mytoken123", "mytoken123", id="lowercase"),
            pytest.param("BEARER mytoken123", "mytoken123", id="uppercase"),
            pytest.param("", None, id="empty"),
            pytest.param("Bearer", None, id="no-token"),
            pytest.param("Basic dXNlcjpwYXNz", None, id="wrong-scheme"),
            pytest.param(
                "Bearer token with spaces",
                "token with spaces",
                id="token-with-spaces",
            ),
        ],
    )
    def test_extract_bearer_token(
        self,
        header: str,
        expected: str | None,
    ) -> None:
        from synthorg.api.auth.middleware import _extract_bearer_token

        assert _extract_bearer_token(header) == expected


@pytest.mark.unit
class TestAuthMiddlewareExcludePaths:
    async def test_excluded_path_skips_auth(self) -> None:
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(
            auth_service=svc,
            persistence=persistence,
            exclude_paths=("/public",),
        )

        with TestClient(app) as client:
            resp = client.get("/public")
            assert resp.status_code == 200


@pytest.mark.unit
class TestAuthMiddlewareSystemUser:
    """System user JWTs (from CLI) skip pwd_sig validation."""

    async def test_system_user_jwt_without_pwd_sig_authenticates(
        self,
    ) -> None:
        """CLI-style JWT with sub=system and no pwd_sig passes auth."""
        import jwt as pyjwt

        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()

        # Create system user with random hash (like ensure_system_user does)
        now = datetime.now(UTC)
        system_user = User(
            id="system",
            username="system",
            password_hash=svc.hash_password("random-password-12chars"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(system_user)

        # Build a CLI-style JWT (no pwd_sig, iss=synthorg-cli, aud=synthorg-backend)
        token = pyjwt.encode(
            {
                "sub": "system",
                "iss": "synthorg-cli",
                "aud": "synthorg-backend",
                "jti": "sys-jti-1",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_system_user_resolves_to_system_role(self) -> None:
        """Middleware resolves system user JWT to SYSTEM role."""
        import jwt as pyjwt

        from synthorg.api.auth.middleware import _resolve_jwt_user

        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()

        now = datetime.now(UTC)
        system_user = User(
            id="system",
            username="system",
            password_hash=svc.hash_password("random-password-12chars"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(system_user)

        # Build CLI-style claims (no pwd_sig, iss=synthorg-cli, aud=synthorg-backend)
        claims = svc.decode_token(
            pyjwt.encode(
                {
                    "sub": "system",
                    "iss": "synthorg-cli",
                    "aud": "synthorg-backend",
                    "jti": "sys-jti-3",
                    "iat": now,
                    "exp": now + timedelta(seconds=60),
                },
                _SECRET,
                algorithm="HS256",
            ),
        )

        class _FakeAppState:
            def __init__(self) -> None:
                self.persistence = persistence

        result = await _resolve_jwt_user(
            claims,
            _FakeAppState(),  # type: ignore[arg-type]
            "/admin/backups",
        )
        assert result is not None
        assert result.role == HumanRole.SYSTEM
        assert result.user_id == "system"

    async def test_system_user_jwt_with_wrong_issuer_returns_401(
        self,
    ) -> None:
        """System user JWT with wrong iss is rejected."""
        import jwt as pyjwt

        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()

        now = datetime.now(UTC)
        system_user = User(
            id="system",
            username="system",
            password_hash=svc.hash_password("random-password-12chars"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(system_user)

        # JWT has sub=system but wrong iss
        token = pyjwt.encode(
            {
                "sub": "system",
                "iss": "attacker",
                "aud": "synthorg-backend",
                "jti": "sys-wrong-iss",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

    async def test_system_user_jwt_with_wrong_audience_returns_401(
        self,
    ) -> None:
        """System user JWT with wrong aud is rejected."""
        import jwt as pyjwt

        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()

        now = datetime.now(UTC)
        system_user = User(
            id="system",
            username="system",
            password_hash=svc.hash_password("random-password-12chars"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(system_user)

        # JWT has correct iss but wrong aud
        token = pyjwt.encode(
            {
                "sub": "system",
                "iss": "synthorg-cli",
                "aud": "wrong-audience",
                "jti": "sys-wrong-aud",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

    async def test_system_user_jwt_with_pwd_sig_still_authenticates(
        self,
    ) -> None:
        """System user JWT with an extra pwd_sig claim still works."""
        import jwt as pyjwt

        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()

        now = datetime.now(UTC)
        system_user = User(
            id="system",
            username="system",
            password_hash=svc.hash_password("random-password-12chars"),
            role=HumanRole.SYSTEM,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(system_user)

        # JWT includes a stale pwd_sig -- should be ignored for system users
        token = pyjwt.encode(
            {
                "sub": "system",
                "iss": "synthorg-cli",
                "aud": "synthorg-backend",
                "pwd_sig": "stale-signature-",
                "jti": "sys-jti-2",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_non_system_user_without_pwd_sig_returns_401(self) -> None:
        """Regular user JWT without pwd_sig is still rejected."""
        import jwt as pyjwt

        from synthorg.api.auth.system_user import USER_AUDIENCE, USER_ISSUER

        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        # Build a JWT for a regular user WITHOUT pwd_sig
        now = datetime.now(UTC)
        token = pyjwt.encode(
            {
                "iss": USER_ISSUER,
                "aud": USER_AUDIENCE,
                "sub": user.id,
                "jti": "no-pwd-sig",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

    async def test_user_jwt_with_wrong_issuer_returns_401(self) -> None:
        """User JWT with wrong iss is rejected so a CLI token can't replay."""
        import jwt as pyjwt

        from synthorg.api.auth.system_user import USER_AUDIENCE

        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        now = datetime.now(UTC)
        # Issuer set to the SYSTEM CLI value -- replay attempt.
        token = pyjwt.encode(
            {
                "iss": "synthorg-cli",
                "aud": USER_AUDIENCE,
                "sub": user.id,
                "pwd_sig": _pwd_sig(user.password_hash),
                "jti": "wrong-iss",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

    async def test_user_jwt_with_wrong_audience_returns_401(self) -> None:
        """User JWT with wrong aud is rejected."""
        import jwt as pyjwt

        from synthorg.api.auth.system_user import USER_ISSUER

        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        now = datetime.now(UTC)
        token = pyjwt.encode(
            {
                "iss": USER_ISSUER,
                "aud": "synthorg-backend",
                "sub": user.id,
                "pwd_sig": _pwd_sig(user.password_hash),
                "jti": "wrong-aud",
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            _SECRET,
            algorithm="HS256",
        )

        app = _build_app(auth_service=svc, persistence=persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401


@pytest.mark.unit
class TestAuthMiddlewareCookieAuth:
    """Cookie-based JWT authentication tests."""

    async def test_valid_jwt_in_cookie_authenticates(self) -> None:
        """JWT delivered via session cookie is accepted."""
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        app = _build_app(auth_service=svc, persistence=persistence)
        token, _, _ = svc.create_token(user)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Cookie": f"session={token}"},
            )
            assert resp.status_code == 200

    async def test_invalid_jwt_in_cookie_falls_back_to_header(self) -> None:
        """Bad cookie JWT falls back to Authorization header."""
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        app = _build_app(auth_service=svc, persistence=persistence)
        token, _, _ = svc.create_token(user)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={
                    "Cookie": "session=bad.jwt.token",
                    "Authorization": f"Bearer {token}",
                },
            )
            assert resp.status_code == 200

    async def test_cookie_takes_precedence_over_header(self) -> None:
        """When both cookie and header are present, cookie wins."""
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        app = _build_app(auth_service=svc, persistence=persistence)
        token, _, _ = svc.create_token(user)

        with TestClient(app) as client:
            # Cookie has valid token, header has garbage
            resp = client.get(
                "/protected",
                headers={
                    "Cookie": f"session={token}",
                    "Authorization": "Bearer garbage.jwt.token",
                },
            )
            assert resp.status_code == 200

    async def test_no_cookie_no_header_returns_401(self) -> None:
        """Neither cookie nor header present returns 401."""
        svc = _make_auth_service()
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get("/protected")
            assert resp.status_code == 401

    async def test_cookie_revoked_session_returns_401(self) -> None:
        """Revoked session via cookie is rejected."""
        from unittest.mock import MagicMock

        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        token, _, session_id = svc.create_token(user)

        mock_store = MagicMock()
        mock_store.is_revoked.return_value = True

        auth_config = AuthConfig(jwt_secret=_SECRET)

        @get("/protected")
        async def protected_route() -> dict[str, str]:
            return {"status": "ok"}

        middleware_cls = create_auth_middleware_class(auth_config)

        app = Litestar(
            route_handlers=[protected_route],
            middleware=[middleware_cls],
        )
        app.state["app_state"] = _FakeState(
            auth_service=svc,
            persistence=persistence,
            has_session_store=True,
            session_store=mock_store,
            config=RootConfig(company_name="test"),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Cookie": f"session={token}"},
            )
            assert resp.status_code == 401

        mock_store.is_revoked.assert_called_once_with(session_id)

    async def test_cookie_with_custom_name(self) -> None:
        """Middleware reads from the configured cookie name."""
        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        auth_config = AuthConfig(
            jwt_secret=_SECRET,
            cookie_name="custom_session",
        )

        @get("/protected")
        async def protected_route() -> dict[str, str]:
            return {"status": "ok"}

        middleware_cls = create_auth_middleware_class(auth_config)

        app = Litestar(
            route_handlers=[protected_route],
            middleware=[middleware_cls],
        )
        app.state["app_state"] = _FakeState(
            auth_service=svc,
            persistence=persistence,
            config=RootConfig(
                company_name="test",
                api=ApiConfig(auth=auth_config),
            ),
        )

        token, _, _ = svc.create_token(user)

        with TestClient(app) as client:
            # Default cookie name should NOT work
            resp = client.get(
                "/protected",
                headers={"Cookie": f"session={token}"},
            )
            assert resp.status_code == 401

            # Custom cookie name should work
            resp = client.get(
                "/protected",
                headers={"Cookie": f"custom_session={token}"},
            )
            assert resp.status_code == 200
