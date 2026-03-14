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
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 8000

    def test_cors_defaults(self) -> None:
        cors = CorsConfig()
        assert "http://localhost:5173" in cors.allowed_origins
        assert "GET" in cors.allow_methods

    def test_rate_limit_defaults(self) -> None:
        rl = RateLimitConfig()
        assert rl.max_requests == 100
        assert rl.time_unit == RateLimitTimeUnit.MINUTE
        assert rl.time_unit.value == "minute"
        assert "/api/v1/health" in rl.exclude_paths

    def test_rate_limit_time_unit_values(self) -> None:
        for unit in RateLimitTimeUnit:
            rl = RateLimitConfig(time_unit=unit)
            assert rl.time_unit == unit

    def test_server_ws_ping_defaults(self) -> None:
        server = ServerConfig()
        assert server.ws_ping_interval == 20.0
        assert server.ws_ping_timeout == 20.0

    def test_server_custom_values(self) -> None:
        server = ServerConfig(host="0.0.0.0", port=9000)  # noqa: S104
        assert server.host == "0.0.0.0"  # noqa: S104
        assert server.port == 9000

    def test_custom_cors_origins(self) -> None:
        cors = CorsConfig(allowed_origins=("https://example.com",))
        assert cors.allowed_origins == ("https://example.com",)

    def test_cors_wildcard_with_credentials_rejected(self) -> None:
        with pytest.raises(ValidationError, match="incompatible"):
            CorsConfig(allowed_origins=("*",), allow_credentials=True)

    def test_cors_wildcard_without_credentials_ok(self) -> None:
        cors = CorsConfig(allowed_origins=("*",), allow_credentials=False)
        assert "*" in cors.allowed_origins

    def test_cors_credentials_without_wildcard_ok(self) -> None:
        cors = CorsConfig(
            allowed_origins=("https://example.com",),
            allow_credentials=True,
        )
        assert cors.allow_credentials is True

    def test_frozen(self) -> None:
        config = ApiConfig()
        with pytest.raises(ValidationError):
            config.api_prefix = "/other"  # type: ignore[misc]
