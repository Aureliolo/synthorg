"""Tests for API configuration models."""

import pytest

from ai_company.api.config import (
    ApiConfig,
    CorsConfig,
    RateLimitConfig,
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
        assert "100/minute" in rl.rate_limit
        assert "/api/v1/health" in rl.exclude_paths

    def test_server_custom_values(self) -> None:
        server = ServerConfig(host="0.0.0.0", port=9000)  # noqa: S104
        assert server.host == "0.0.0.0"  # noqa: S104
        assert server.port == 9000

    def test_custom_cors_origins(self) -> None:
        cors = CorsConfig(allowed_origins=("https://example.com",))
        assert cors.allowed_origins == ("https://example.com",)

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        config = ApiConfig()
        with pytest.raises(ValidationError):
            config.api_prefix = "/other"  # type: ignore[misc]
