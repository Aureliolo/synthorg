"""Tests for ApiAuthMiddleware."""

from datetime import UTC, datetime

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from ai_company.api.auth.config import AuthConfig
from ai_company.api.auth.middleware import create_auth_middleware_class
from ai_company.api.auth.models import ApiKey, User
from ai_company.api.auth.service import AuthService
from ai_company.api.guards import HumanRole
from tests.unit.api.conftest import FakePersistenceBackend

_SECRET = "test-secret-that-is-at-least-32-characters-long"


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
        key_hash = AuthService.hash_api_key(raw_key)
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
        key_hash = AuthService.hash_api_key(raw_key)
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
        from datetime import timedelta

        svc = _make_auth_service()
        user = _make_user(svc)
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.users.save(user)

        raw_key = AuthService.generate_api_key()
        key_hash = AuthService.hash_api_key(raw_key)
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
        key_hash = AuthService.hash_api_key(raw_key)
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
