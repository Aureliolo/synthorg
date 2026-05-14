"""Tests for API configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.api.config import (
    ApiConfig,
    CorsConfig,
    RateLimitConfig,
    RateLimitTimeUnit,
    ServerConfig,
)


@pytest.mark.unit
class TestApiConfig:
    def test_defaults(self) -> None:
        config = ApiConfig()
        assert config.api_prefix == "/api/v1"

    def test_cors_defaults(self) -> None:
        # CFG-1 audit flipped the default to an empty tuple so
        # production deployments never accidentally allow the Vite
        # dev origin. Operators opt in explicitly via the setting.
        cors = CorsConfig()
        assert cors.allowed_origins == ()
        assert "GET" in cors.allow_methods

    def test_rate_limit_defaults(self) -> None:
        rl = RateLimitConfig()
        assert rl.floor_max_requests == 10000
        assert rl.unauth_max_requests == 20
        assert rl.auth_max_requests == 6000
        assert rl.time_unit == RateLimitTimeUnit.MINUTE
        assert rl.time_unit.value == "minute"
        assert "/api/v1/healthz" in rl.exclude_paths
        assert "/api/v1/readyz" in rl.exclude_paths
        # Default floor must be >= default auth cap -- otherwise the
        # authenticated per-user budget is clipped by the floor.
        assert rl.floor_max_requests >= rl.auth_max_requests

    def test_rate_limit_floor_below_auth_rejected(self) -> None:
        # Regression guard: a floor below the authenticated cap makes
        # the documented per-user budget unreachable because the floor
        # wraps the authenticated tier in the middleware stack.
        with pytest.raises(
            ValidationError,
            match=r"floor_max_requests=.*must be >= auth_max_requests",
        ):
            RateLimitConfig(
                floor_max_requests=100,
                auth_max_requests=6000,
            )

    def test_rate_limit_custom_values(self) -> None:
        rl = RateLimitConfig(
            unauth_max_requests=10,
            auth_max_requests=1000,
        )
        assert rl.unauth_max_requests == 10
        assert rl.auth_max_requests == 1000

    def test_rate_limit_legacy_max_requests_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"max_requests.*replaced",
        ):
            RateLimitConfig(max_requests=100)  # type: ignore[call-arg]

    def test_rate_limit_time_unit_values(self) -> None:
        for unit in RateLimitTimeUnit:
            rl = RateLimitConfig(time_unit=unit)
            assert rl.time_unit == unit

    def test_rate_limit_frozen(self) -> None:
        rl = RateLimitConfig()
        with pytest.raises(ValidationError):
            rl.unauth_max_requests = 50  # type: ignore[misc]

    def test_server_ws_ping_defaults(self) -> None:
        server = ServerConfig()
        assert server.ws_ping_interval == 20.0
        assert server.ws_ping_timeout == 20.0

    def test_cors_credentials_default(self) -> None:
        cors = CorsConfig()
        assert cors.allow_credentials is True

    def test_frozen(self) -> None:
        config = ApiConfig()
        with pytest.raises(ValidationError):
            config.api_prefix = "/other"  # type: ignore[misc]
