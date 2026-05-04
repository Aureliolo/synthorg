"""Tests for AuthConfig."""

import pytest

from synthorg.core.auth.config import AuthConfig


@pytest.mark.unit
class TestAuthConfig:
    def test_default_values(self) -> None:
        config = AuthConfig()
        assert config.jwt_secret == ""
        assert config.jwt_algorithm == "HS256"
        assert config.jwt_expiry_minutes == 1440
        assert config.exclude_paths is None

    def test_explicit_exclude_paths(self) -> None:
        paths = ("^/health$", "^/docs")
        config = AuthConfig(exclude_paths=paths)
        assert config.exclude_paths == paths

    def test_with_secret_sets_secret(self) -> None:
        config = AuthConfig()
        updated = config.with_secret(
            "a-very-long-secret-that-is-at-least-32-characters"
        )
        assert updated.jwt_secret == "a-very-long-secret-that-is-at-least-32-characters"

    def test_with_secret_too_short_raises(self) -> None:
        config = AuthConfig()
        with pytest.raises(ValueError, match="at least 32"):
            config.with_secret("short")

    def test_frozen(self) -> None:
        config = AuthConfig()
        with pytest.raises(Exception):  # noqa: B017, PT011
            config.jwt_secret = "new"  # type: ignore[misc]

    def test_original_unchanged_after_with_secret(self) -> None:
        config = AuthConfig()
        config.with_secret("a-very-long-secret-that-is-at-least-32-characters")
        assert config.jwt_secret == ""

    def test_custom_expiry(self) -> None:
        config = AuthConfig(jwt_expiry_minutes=60)
        assert config.jwt_expiry_minutes == 60

    def test_expiry_min_bound(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            AuthConfig(jwt_expiry_minutes=0)

    def test_expiry_max_bound(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            AuthConfig(jwt_expiry_minutes=50000)

    # ── Cookie settings ──────────────────────────────────────

    def test_cookie_defaults(self) -> None:
        config = AuthConfig()
        assert config.cookie_name == "session"
        assert config.cookie_secure is True
        assert config.cookie_samesite == "strict"
        assert config.cookie_path == "/api"
        assert config.cookie_domain is None

    @pytest.mark.parametrize("value", ["strict", "lax", "none"])
    def test_cookie_samesite_accepts_valid(self, value: str) -> None:
        config = AuthConfig(cookie_samesite=value)  # type: ignore[arg-type]
        assert config.cookie_samesite == value

    def test_cookie_samesite_rejects_invalid(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            AuthConfig(cookie_samesite="invalid")  # type: ignore[arg-type]

    # ── CSRF settings ────────────────────────────────────────

    def test_csrf_defaults(self) -> None:
        config = AuthConfig()
        assert config.csrf_cookie_name == "csrf_token"
        assert config.csrf_header_name == "x-csrf-token"
        # CSRF cookie path must be '/' so document.cookie can read it
        # from any SPA route; the session cookie stays scoped to '/api'.
        assert config.csrf_cookie_path == "/"

    # ── Concurrent sessions ──────────────────────────────────

    def test_max_sessions_default(self) -> None:
        config = AuthConfig()
        assert config.max_concurrent_sessions == 5

    def test_max_sessions_zero_unlimited(self) -> None:
        config = AuthConfig(max_concurrent_sessions=0)
        assert config.max_concurrent_sessions == 0

    def test_max_sessions_upper_bound(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            AuthConfig(max_concurrent_sessions=101)

    # ── Refresh tokens ───────────────────────────────────────

    def test_refresh_defaults(self) -> None:
        config = AuthConfig()
        assert config.jwt_refresh_enabled is False
        assert config.jwt_refresh_expiry_minutes == 10080
        assert config.refresh_cookie_name == "refresh_token"
        assert config.refresh_cookie_path == "/api/v1/auth/refresh"

    # ── Account lockout ──────────────────────────────────────

    def test_lockout_defaults(self) -> None:
        config = AuthConfig()
        assert config.lockout_threshold == 10
        assert config.lockout_window_minutes == 15
        assert config.lockout_duration_minutes == 15

    def test_lockout_threshold_min_bound(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            AuthConfig(lockout_threshold=0)

    def test_lockout_threshold_max_bound(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            AuthConfig(lockout_threshold=101)
