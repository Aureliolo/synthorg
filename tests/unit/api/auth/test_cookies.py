"""Tests for cookie creation helpers."""

import pytest

from synthorg.api.auth.cookies import (
    generate_csrf_token,
    make_clear_csrf_cookie,
    make_clear_refresh_cookie,
    make_clear_session_cookie,
    make_csrf_cookie,
    make_refresh_cookie,
    make_session_cookie,
)
from synthorg.core.auth.config import AuthConfig


@pytest.mark.unit
class TestMakeSessionCookie:
    def test_creates_httponly_cookie(self) -> None:
        config = AuthConfig()
        cookie = make_session_cookie("jwt.token.here", 3600, config)
        assert cookie.key == "session"
        assert cookie.value == "jwt.token.here"
        assert cookie.httponly is True
        assert cookie.secure is True
        assert cookie.samesite == "strict"
        assert cookie.path == "/api"
        assert cookie.domain is None
        assert cookie.max_age == 3600

    def test_respects_custom_config(self) -> None:
        config = AuthConfig(
            cookie_name="custom_session",
            cookie_secure=False,
            cookie_samesite="lax",
            cookie_path="/custom",
            cookie_domain="example.com",
        )
        cookie = make_session_cookie("tok", 600, config)
        assert cookie.key == "custom_session"
        assert cookie.secure is False
        assert cookie.samesite == "lax"
        assert cookie.path == "/custom"
        assert cookie.domain == "example.com"
        assert cookie.max_age == 600


@pytest.mark.unit
class TestMakeCsrfCookie:
    def test_not_httponly(self) -> None:
        config = AuthConfig()
        cookie = make_csrf_cookie("csrf-token-value", 3600, config)
        assert cookie.key == "csrf_token"
        assert cookie.httponly is False
        assert cookie.secure is True
        assert cookie.samesite == "strict"
        assert cookie.value == "csrf-token-value"

    def test_uses_csrf_cookie_name(self) -> None:
        config = AuthConfig(csrf_cookie_name="xsrf")
        cookie = make_csrf_cookie("tok", 100, config)
        assert cookie.key == "xsrf"


@pytest.mark.unit
class TestMakeRefreshCookie:
    def test_httponly_with_narrow_path(self) -> None:
        config = AuthConfig()
        cookie = make_refresh_cookie("refresh-tok", 604800, config)
        assert cookie.key == "refresh_token"
        assert cookie.httponly is True
        assert cookie.secure is True
        assert cookie.path == "/api/v1/auth/refresh"
        assert cookie.max_age == 604800

    def test_custom_refresh_path(self) -> None:
        config = AuthConfig(refresh_cookie_path="/custom/refresh")
        cookie = make_refresh_cookie("tok", 100, config)
        assert cookie.path == "/custom/refresh"


@pytest.mark.unit
class TestClearCookies:
    def test_clear_session_cookie(self) -> None:
        config = AuthConfig()
        cookie = make_clear_session_cookie(config)
        assert cookie.key == "session"
        assert cookie.value == ""
        assert cookie.max_age == 0
        assert cookie.httponly is True

    def test_clear_csrf_cookie(self) -> None:
        config = AuthConfig()
        cookie = make_clear_csrf_cookie(config)
        assert cookie.key == "csrf_token"
        assert cookie.value == ""
        assert cookie.max_age == 0
        assert cookie.httponly is False

    def test_clear_refresh_cookie(self) -> None:
        config = AuthConfig()
        cookie = make_clear_refresh_cookie(config)
        assert cookie.key == "refresh_token"
        assert cookie.value == ""
        assert cookie.max_age == 0
        assert cookie.httponly is True
        assert cookie.path == "/api/v1/auth/refresh"


@pytest.mark.unit
class TestGenerateCsrfToken:
    def test_returns_non_empty_string(self) -> None:
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_generates_unique_tokens(self) -> None:
        tokens = {generate_csrf_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_url_safe(self) -> None:
        token = generate_csrf_token()
        safe_chars = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
        assert all(c in safe_chars for c in token)
