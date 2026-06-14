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
from synthorg.config.rate_limits import (
    PerOpConcurrencyConfig,
    PerOpRateLimitConfig,
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
            match=r"[Ee]xtra inputs are not permitted",
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


@pytest.mark.unit
class TestRateLimitConfigMirrors:
    """Direct coverage for the settings mirror integration on RateLimitConfig.

    The five mirrored fields (unauth_max_requests, auth_max_requests,
    time_unit, exclude_paths, max_rpm_default) each have their own
    env-var override path; these tests exercise the three branches that
    matter (env-set, env-unset, env-invalid) and the caller-wins
    invariant.
    """

    def test_env_override_beats_registered_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMIT_UNAUTH_MAX_REQUESTS", "7")
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMIT_AUTH_MAX_REQUESTS", "1234")
        monkeypatch.setenv("SYNTHORG_API_MAX_RPM_DEFAULT", "300")
        rl = RateLimitConfig()
        assert rl.unauth_max_requests == 7
        assert rl.auth_max_requests == 1234
        assert rl.max_rpm_default == 300

    def test_env_override_for_exclude_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_RATE_LIMIT_EXCLUDE_PATHS",
            '["/internal/metrics"]',
        )
        rl = RateLimitConfig()
        assert rl.exclude_paths == ("/internal/metrics",)

    def test_env_override_for_time_unit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMIT_TIME_UNIT", "hour")
        rl = RateLimitConfig()
        assert rl.time_unit == RateLimitTimeUnit.HOUR

    def test_caller_kwarg_wins_over_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMIT_UNAUTH_MAX_REQUESTS", "7")
        rl = RateLimitConfig(unauth_max_requests=50)
        assert rl.unauth_max_requests == 50

    def test_invalid_env_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_RATE_LIMIT_UNAUTH_MAX_REQUESTS",
            "not-a-number",
        )
        rl = RateLimitConfig()
        assert rl.unauth_max_requests == 20

    def test_invalid_exclude_paths_json_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_RATE_LIMIT_EXCLUDE_PATHS",
            "{not valid json",
        )
        rl = RateLimitConfig()
        assert "/api/v1/healthz" in rl.exclude_paths


@pytest.mark.unit
class TestPerOpOverridesMirror:
    """Coverage for the per-operation overrides mirror integration.

    The JSON-typed env vars ``SYNTHORG_API_PER_OP_RATE_LIMIT_OVERRIDES``
    and ``SYNTHORG_API_PER_OP_CONCURRENCY_OVERRIDES`` populate the
    Pydantic ``overrides`` dict on construction, then the existing
    ``mode='before'`` shape validators run on the populated dict.
    """

    def test_rate_limit_overrides_env_populates_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_RATE_LIMIT_OVERRIDES",
            '{"memory.fine_tune":[2,3600]}',
        )
        cfg = PerOpRateLimitConfig()
        assert cfg.overrides == {"memory.fine_tune": (2, 3600)}

    def test_rate_limit_overrides_empty_env_keeps_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_API_PER_OP_RATE_LIMIT_OVERRIDES", raising=False)
        cfg = PerOpRateLimitConfig()
        assert cfg.overrides == {}

    def test_rate_limit_overrides_invalid_env_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_RATE_LIMIT_OVERRIDES",
            "{not valid json",
        )
        cfg = PerOpRateLimitConfig()
        assert cfg.overrides == {}

    def test_rate_limit_overrides_caller_kwarg_wins_over_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_RATE_LIMIT_OVERRIDES",
            '{"memory.fine_tune":[2,3600]}',
        )
        cfg = PerOpRateLimitConfig(overrides={"meetings.invite": (5, 60)})
        assert cfg.overrides == {"meetings.invite": (5, 60)}

    def test_rate_limit_overrides_env_with_negative_value_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_RATE_LIMIT_OVERRIDES",
            '{"memory.fine_tune":[-1,3600]}',
        )
        with pytest.raises(ValidationError, match="non-negative"):
            PerOpRateLimitConfig()

    def test_concurrency_overrides_env_populates_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_CONCURRENCY_OVERRIDES",
            '{"memory.fine_tune":1}',
        )
        cfg = PerOpConcurrencyConfig()
        assert cfg.overrides == {"memory.fine_tune": 1}

    def test_concurrency_overrides_empty_env_keeps_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_API_PER_OP_CONCURRENCY_OVERRIDES", raising=False)
        cfg = PerOpConcurrencyConfig()
        assert cfg.overrides == {}

    def test_concurrency_overrides_invalid_env_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_CONCURRENCY_OVERRIDES",
            "{not valid json",
        )
        cfg = PerOpConcurrencyConfig()
        assert cfg.overrides == {}

    def test_concurrency_overrides_env_with_negative_value_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_PER_OP_CONCURRENCY_OVERRIDES",
            '{"memory.fine_tune":-1}',
        )
        with pytest.raises(ValidationError, match="non-negative"):
            PerOpConcurrencyConfig()
