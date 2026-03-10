"""Tests for AuthConfig."""

import pytest

from ai_company.api.auth.config import AuthConfig


@pytest.mark.unit
class TestAuthConfig:
    def test_default_values(self) -> None:
        config = AuthConfig()
        assert config.jwt_secret == ""
        assert config.jwt_algorithm == "HS256"
        assert config.jwt_expiry_minutes == 1440
        assert "^/api/v1/health$" in config.exclude_paths
        assert "^/api/v1/auth/setup$" in config.exclude_paths
        assert "^/api/v1/auth/login$" in config.exclude_paths

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
