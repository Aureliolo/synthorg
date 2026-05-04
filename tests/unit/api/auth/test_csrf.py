"""Tests for CSRF middleware."""

import pytest
from litestar import Litestar, get, post
from litestar.testing import TestClient

from synthorg.api.auth.csrf import create_csrf_middleware_class
from synthorg.core.auth.config import AuthConfig


def _build_csrf_app(
    *,
    auth_config: AuthConfig | None = None,
    exempt_paths: frozenset[str] | None = None,
) -> Litestar:
    """Build a minimal Litestar app with CSRF middleware."""
    config = auth_config or AuthConfig()

    @get("/data")
    async def get_data() -> dict[str, str]:
        return {"status": "ok"}

    @post("/mutate")
    async def mutate_data() -> dict[str, str]:
        return {"status": "mutated"}

    csrf_cls = create_csrf_middleware_class(
        config,
        exempt_paths=exempt_paths,
    )
    return Litestar(
        route_handlers=[get_data, mutate_data],
        middleware=[csrf_cls],
    )


@pytest.mark.unit
class TestCsrfSafeMethods:
    def test_get_always_passes(self) -> None:
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.get("/data")
            assert resp.status_code == 200


@pytest.mark.unit
class TestCsrfNoSessionCookie:
    def test_post_without_session_cookie_passes(self) -> None:
        """No session cookie -> no CSRF risk -> skip validation."""
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.post("/mutate")
            assert resp.status_code == 201

    def test_post_with_api_key_header_no_cookie_passes(self) -> None:
        """API key auth (no cookie) should not be CSRF-gated."""
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.post(
                "/mutate",
                headers={"Authorization": "Bearer some-api-key"},
            )
            assert resp.status_code == 201


@pytest.mark.unit
class TestCsrfWithSessionCookie:
    def test_post_with_cookie_but_no_csrf_token_returns_403(self) -> None:
        """Session cookie present but no CSRF token -> reject."""
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.post(
                "/mutate",
                headers={"Cookie": "session=some.jwt.token"},
            )
            assert resp.status_code == 403

    def test_post_with_matching_csrf_tokens_passes(self) -> None:
        """Session cookie + matching CSRF cookie and header -> accept."""
        csrf_value = "test-csrf-token-value"
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.post(
                "/mutate",
                headers={
                    "Cookie": f"session=some.jwt.token; csrf_token={csrf_value}",
                    "X-CSRF-Token": csrf_value,
                },
            )
            assert resp.status_code == 201

    def test_post_with_mismatched_csrf_tokens_returns_403(self) -> None:
        """CSRF header doesn't match cookie -> reject."""
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.post(
                "/mutate",
                headers={
                    "Cookie": "session=some.jwt.token; csrf_token=correct-token",
                    "X-CSRF-Token": "wrong-token",
                },
            )
            assert resp.status_code == 403

    def test_post_with_csrf_cookie_but_no_header_returns_403(self) -> None:
        """CSRF cookie present but header missing -> reject."""
        app = _build_csrf_app()
        with TestClient(app) as client:
            resp = client.post(
                "/mutate",
                headers={
                    "Cookie": "session=some.jwt.token; csrf_token=some-token",
                },
            )
            assert resp.status_code == 403


@pytest.mark.unit
class TestCsrfExemptPaths:
    def test_exempt_path_skips_csrf(self) -> None:
        """Configured exempt paths skip CSRF validation."""

        @post("/auth/login")
        async def login() -> dict[str, str]:
            return {"status": "logged_in"}

        config = AuthConfig()
        csrf_cls = create_csrf_middleware_class(
            config,
            exempt_paths=frozenset({"/auth/login"}),
        )
        app = Litestar(
            route_handlers=[login],
            middleware=[csrf_cls],
        )

        with TestClient(app) as client:
            # POST to exempt path with session cookie but no CSRF -> should pass
            resp = client.post(
                "/auth/login",
                headers={"Cookie": "session=some.jwt.token"},
            )
            assert resp.status_code == 201


@pytest.mark.unit
class TestCsrfCustomConfig:
    def test_custom_cookie_and_header_names(self) -> None:
        """Middleware respects custom CSRF cookie/header names."""
        config = AuthConfig(
            cookie_name="my_session",
            csrf_cookie_name="xsrf",
            csrf_header_name="x-xsrf-token",
        )
        csrf_value = "custom-csrf-val"
        app = _build_csrf_app(auth_config=config)
        with TestClient(app) as client:
            # No session cookie -> passes
            resp = client.post("/mutate")
            assert resp.status_code == 201

            # Session cookie present, correct CSRF header
            resp = client.post(
                "/mutate",
                headers={
                    "Cookie": f"my_session=some.jwt.token; xsrf={csrf_value}",
                    "X-XSRF-Token": csrf_value,
                },
            )
            assert resp.status_code == 201

            # Wrong header name -> fail
            resp = client.post(
                "/mutate",
                headers={
                    "Cookie": f"my_session=some.jwt.token; xsrf={csrf_value}",
                    "X-CSRF-Token": csrf_value,
                },
            )
            assert resp.status_code == 403
