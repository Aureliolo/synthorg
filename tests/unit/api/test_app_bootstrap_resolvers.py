"""Direct coverage for the boot-time helpers in ``api/app.py``.

These resolvers wrap ``resolve_init_value`` for the API-namespace
Cat-2 reads driven from ``create_app`` (rate limiter, compression
limits, CORS origins, trusted proxies). The wider integration tests
exercise them through the full app builder, but a typo'd env var or
a parser regression would otherwise only surface on a real boot.
"""

import pytest

from synthorg.api.lifecycle_helpers.boot_resolvers import (
    resolve_api_int,
    resolve_api_str,
    resolve_api_str_tuple,
    resolve_budget_int,
    resolve_rate_limiter_enabled,
)


@pytest.mark.unit
class TestResolveRateLimiterEnabled:
    def test_env_set_true_returns_true(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMITER_ENABLED", "true")
        assert resolve_rate_limiter_enabled() is True

    def test_env_set_false_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMITER_ENABLED", "false")
        assert resolve_rate_limiter_enabled() is False

    def test_env_unset_falls_through_to_registered_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_API_RATE_LIMITER_ENABLED", raising=False)
        assert resolve_rate_limiter_enabled() is True

    def test_invalid_token_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_RATE_LIMITER_ENABLED", "maybe")
        assert resolve_rate_limiter_enabled() is True


@pytest.mark.unit
class TestResolveApiStrTuple:
    def test_env_set_to_valid_json_list(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_CORS_ALLOWED_ORIGINS",
            '["https://a.example", "https://b.example"]',
        )
        assert resolve_api_str_tuple("cors_allowed_origins") == (
            "https://a.example",
            "https://b.example",
        )

    def test_env_set_to_invalid_json_returns_empty_tuple(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_CORS_ALLOWED_ORIGINS", "{broken")
        # Invalid JSON yields None from the parser; resolver applies the
        # registered default. The default for cors_allowed_origins is
        # `[]` which deserialises to an empty tuple.
        assert resolve_api_str_tuple("cors_allowed_origins") == ()

    def test_env_unset_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_API_CORS_ALLOWED_ORIGINS", raising=False)
        assert resolve_api_str_tuple("cors_allowed_origins") == ()


@pytest.mark.unit
class TestResolveApiInt:
    def test_env_set_to_valid_int(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_API_COMPRESSION_MINIMUM_SIZE_BYTES",
            "2048",
        )
        assert resolve_api_int("compression_minimum_size_bytes") == 2048

    def test_invalid_int_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(
            "SYNTHORG_API_COMPRESSION_MINIMUM_SIZE_BYTES",
            raising=False,
        )
        expected_default = resolve_api_int("compression_minimum_size_bytes")

        # Regression guard: prior to wiring parse_int, resolve_api_int
        # passed bare int() as the parse callback, which raised
        # ValueError uncaught and crashed app construction on any typo.
        monkeypatch.setenv(
            "SYNTHORG_API_COMPRESSION_MINIMUM_SIZE_BYTES",
            "not-a-number",
        )
        # parse_int returns None, resolver falls back to default.
        assert resolve_api_int("compression_minimum_size_bytes") == expected_default


@pytest.mark.unit
class TestResolveApiStr:
    def test_env_set_returns_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_API_API_PREFIX", "/api/v2")
        assert resolve_api_str("api_prefix") == "/api/v2"

    def test_env_unset_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_API_API_PREFIX", raising=False)
        assert resolve_api_str("api_prefix") == "/api/v1"


@pytest.mark.unit
class TestResolveBudgetInt:
    """Cat-2 boot resolution for ``budget.coordination_metrics_max_entries``.

    The ``CoordinationMetricsStore`` ring buffer is sized before the
    ``SettingsService`` connects, so the value is env > registered
    default via the bootstrap resolver.
    """

    _ENV = "SYNTHORG_BUDGET_COORDINATION_METRICS_MAX_ENTRIES"

    def test_env_set_to_valid_int(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(self._ENV, "250")
        assert resolve_budget_int("coordination_metrics_max_entries") == 250

    def test_env_unset_returns_registered_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(self._ENV, raising=False)
        assert resolve_budget_int("coordination_metrics_max_entries") == 10000

    def test_invalid_int_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(self._ENV, raising=False)
        expected_default = resolve_budget_int("coordination_metrics_max_entries")
        # parse_int returns None on a non-numeric env so the resolver
        # falls back to the registered default rather than crashing
        # app construction.
        monkeypatch.setenv(self._ENV, "not-a-number")
        assert (
            resolve_budget_int("coordination_metrics_max_entries") == expected_default
        )


@pytest.mark.unit
class TestResolveBaselineWindowSize:
    """Cat-2 boot resolution for ``budget.baseline_window_size``.

    The ``BaselineStore`` sliding window is sized before the
    ``SettingsService`` connects, so the value is env > registered
    default via the bootstrap resolver.
    """

    _ENV = "SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE"

    def test_env_set_to_valid_int(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(self._ENV, "120")
        assert resolve_budget_int("baseline_window_size") == 120

    def test_env_unset_returns_registered_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(self._ENV, raising=False)
        assert resolve_budget_int("baseline_window_size") == 50

    def test_invalid_int_falls_through_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(self._ENV, raising=False)
        expected_default = resolve_budget_int("baseline_window_size")
        monkeypatch.setenv(self._ENV, "not-a-number")
        assert resolve_budget_int("baseline_window_size") == expected_default
