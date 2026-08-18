"""Tests for CSRF middleware."""

import re
from typing import Final

import pytest
from litestar import Litestar, get, post

from synthorg.api.auth.csrf import create_csrf_middleware_class
from synthorg.core.auth.config import AuthConfig
from synthorg.core.error_taxonomy import (
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)
from tests._shared import LoopAsyncClient

_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
)


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
    async def test_get_always_passes(self) -> None:
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.get("/data")
            assert resp.status_code == 200


@pytest.mark.unit
class TestCsrfNoSessionCookie:
    async def test_post_without_session_cookie_passes(self) -> None:
        """No session cookie -> no CSRF risk -> skip validation."""
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post("/mutate")
            assert resp.status_code == 201

    async def test_post_with_api_key_header_no_cookie_passes(self) -> None:
        """API key auth (no cookie) should not be CSRF-gated."""
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={"Authorization": "Bearer some-api-key"},
            )
            assert resp.status_code == 201


@pytest.mark.unit
class TestCsrfWithSessionCookie:
    async def test_post_with_cookie_but_no_csrf_token_returns_403(self) -> None:
        """Session cookie present but no CSRF token -> reject."""
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={"Cookie": "session=some.jwt.token"},
            )
            assert resp.status_code == 403

    async def test_post_with_matching_csrf_tokens_passes(self) -> None:
        """Session cookie + matching CSRF cookie and header -> accept."""
        csrf_value = "test-csrf-token-value"
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={
                    "Cookie": f"session=some.jwt.token; csrf_token={csrf_value}",
                    "X-CSRF-Token": csrf_value,
                },
            )
            assert resp.status_code == 201

    async def test_post_with_mismatched_csrf_tokens_returns_403(self) -> None:
        """CSRF header doesn't match cookie -> reject."""
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={
                    "Cookie": "session=some.jwt.token; csrf_token=correct-token",
                    "X-CSRF-Token": "wrong-token",
                },
            )
            assert resp.status_code == 403

    async def test_post_with_csrf_cookie_but_no_header_returns_403(self) -> None:
        """CSRF cookie present but header missing -> reject."""
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={
                    "Cookie": "session=some.jwt.token; csrf_token=some-token",
                },
            )
            assert resp.status_code == 403


@pytest.mark.unit
class TestCsrfRejectionEnvelope:
    """A rejection answers in the same envelope every other error uses.

    The middleware writes its response over raw ASGI, before Litestar's
    exception pipeline exists, so nothing forces the shape on it. A
    divergent body leaves a client parsing ``error_detail`` with
    ``None`` on exactly the responses a security control produces, and
    leaves the rejection with no correlation id to tie it back to the
    ``security.csrf.rejected`` log line it already writes.
    """

    async def test_rejection_body_is_the_standard_error_envelope(self) -> None:
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={"Cookie": "session=some.jwt.token"},
            )

        assert resp.status_code == 403
        body = resp.json()
        assert set(body) == {"data", "error", "error_detail", "success"}
        assert body["data"] is None
        assert body["success"] is False

        detail = body["error_detail"]
        assert detail["detail"] == body["error"]
        assert detail["error_code"] == ErrorCode.CSRF_REJECTED
        assert detail["error_category"] == ErrorCategory.AUTH
        assert detail["retryable"] is False
        assert detail["retry_after"] is None
        assert detail["title"] == category_title(ErrorCategory.AUTH)
        assert detail["type"] == category_type_uri(ErrorCategory.AUTH)

    async def test_rejection_carries_a_correlation_id(self) -> None:
        app = _build_csrf_app()
        async with LoopAsyncClient(app) as client:
            resp = await client.post(
                "/mutate",
                headers={"Cookie": "session=some.jwt.token"},
            )

        instance = resp.json()["error_detail"]["instance"]
        assert _UUID_RE.match(instance) is not None


@pytest.mark.unit
class TestCsrfExemptPaths:
    async def test_exempt_path_skips_csrf(self) -> None:
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

        async with LoopAsyncClient(app) as client:
            # POST to exempt path with session cookie but no CSRF -> should pass
            resp = await client.post(
                "/auth/login",
                headers={"Cookie": "session=some.jwt.token"},
            )
            assert resp.status_code == 201


@pytest.mark.unit
class TestCsrfCustomConfig:
    async def test_custom_cookie_and_header_names(self) -> None:
        """Middleware respects custom CSRF cookie/header names."""
        config = AuthConfig(
            cookie_name="my_session",
            csrf_cookie_name="xsrf",
            csrf_header_name="x-xsrf-token",
        )
        csrf_value = "custom-csrf-val"
        app = _build_csrf_app(auth_config=config)
        async with LoopAsyncClient(app) as client:
            # No session cookie -> passes
            resp = await client.post("/mutate")
            assert resp.status_code == 201

            # Session cookie present, correct CSRF header
            resp = await client.post(
                "/mutate",
                headers={
                    "Cookie": f"my_session=some.jwt.token; xsrf={csrf_value}",
                    "X-XSRF-Token": csrf_value,
                },
            )
            assert resp.status_code == 201

            # Wrong header name -> fail
            resp = await client.post(
                "/mutate",
                headers={
                    "Cookie": f"my_session=some.jwt.token; xsrf={csrf_value}",
                    "X-CSRF-Token": csrf_value,
                },
            )
            assert resp.status_code == 403
