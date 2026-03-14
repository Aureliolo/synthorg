"""Tests for ApiAuthMiddleware."""

from datetime import UTC, datetime, timedelta

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.middleware import create_auth_middleware_class
from synthorg.api.auth.models import ApiKey, User
from synthorg.api.auth.service import AuthService
from synthorg.api.guards import HumanRole
from tests.unit.api.conftest import _TEST_JWT_SECRET as _SECRET
from tests.unit.api.conftest import FakePersistenceBackend


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

    class _FakeState:
        def __init__(self) -> None:
            self.auth_service = auth_service
            self.persistence = persistence

    app = Litestar(
        route_handlers=[protected_route, public_route],
        middleware=[middleware_cls],
    )
    app.state["app_state"] = _FakeState()
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
        token, _ = svc.create_token(user)

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

        token, _ = svc.create_token(user)

        # Change password — new hash means different pwd_sig
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
        # Don't save user — simulate deleted user
        token, _ = svc.create_token(user)
        app = _build_app(auth_service=svc, persistence=persistence)

        with TestClient(app) as client:
            resp = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401


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
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        # Save the user, create a key, then delete the user
        await persistence.users.save(user)

        raw_key = AuthService.generate_api_key()
        key_hash = svc.hash_api_key(raw_key)
        now = datetime.now(UTC)
        api_key = ApiKey(
            id="key-orphan",
            key_hash=key_hash,
            name="orphaned-key",
            role=HumanRole.CEO,
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
