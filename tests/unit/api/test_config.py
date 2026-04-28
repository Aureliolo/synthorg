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
        assert config.server.port == 3001

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


@pytest.mark.unit
class TestServerConfigTLS:
    """Tests for TLS and trusted proxy configuration."""

    def test_tls_defaults_none(self) -> None:
        server = ServerConfig()
        assert server.ssl_certfile is None
        assert server.ssl_keyfile is None
        assert server.ssl_ca_certs is None
        assert server.trusted_proxies == ()

    def test_valid_tls_pair(self) -> None:
        server = ServerConfig(
            ssl_certfile="/etc/tls/cert.pem",
            ssl_keyfile="/etc/tls/key.pem",
        )
        assert server.ssl_certfile == "/etc/tls/cert.pem"
        assert server.ssl_keyfile == "/etc/tls/key.pem"

    def test_certfile_without_keyfile_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"ssl_keyfile.*required",
        ):
            ServerConfig(ssl_certfile="/etc/tls/cert.pem")

    def test_keyfile_without_certfile_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"ssl_certfile.*required",
        ):
            ServerConfig(ssl_keyfile="/etc/tls/key.pem")

    def test_tls_with_ca_certs(self) -> None:
        server = ServerConfig(
            ssl_certfile="/etc/tls/cert.pem",
            ssl_keyfile="/etc/tls/key.pem",
            ssl_ca_certs="/etc/tls/ca.pem",
        )
        assert server.ssl_ca_certs == "/etc/tls/ca.pem"

    def test_ca_certs_without_tls_pair_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"ssl_certfile.*required",
        ):
            ServerConfig(ssl_ca_certs="/etc/tls/ca.pem")

    def test_trusted_proxies_accepts_ips(self) -> None:
        server = ServerConfig(
            trusted_proxies=("10.0.0.1", "172.16.0.0/12"),
        )
        assert server.trusted_proxies == ("10.0.0.1", "172.16.0.0/12")

    def test_trusted_proxies_empty_by_default(self) -> None:
        server = ServerConfig()
        assert server.trusted_proxies == ()


@pytest.mark.unit
class TestServerConfigBeforeValidatorImmutability:
    """The empty-string TLS normalizer must not mutate caller input."""

    def test_empty_string_normalization_does_not_mutate_input(self) -> None:
        import copy

        original = {
            "ssl_certfile": "",
            "ssl_keyfile": "  ",
            "ssl_ca_certs": "/etc/tls/ca.pem",
        }
        snapshot = copy.deepcopy(original)
        with pytest.raises(ValidationError):
            ServerConfig.model_validate(original)
        assert original == snapshot, "before-validator mutated caller input"

    def test_input_dict_remains_reusable(self) -> None:
        original = {
            "ssl_certfile": "",
            "ssl_keyfile": "",
        }
        first = ServerConfig.model_validate(original)
        second = ServerConfig.model_validate(original)
        assert first.ssl_certfile is None
        assert second.ssl_certfile is None
        assert original == {"ssl_certfile": "", "ssl_keyfile": ""}
