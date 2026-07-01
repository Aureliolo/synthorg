"""LlmProviderHealthCheck: tracker-first health + reachability fallback.

When a provider-health lookup is bound, the checker reports the provider
tracker's aggregated verdict (the same source the Providers screen shows).
Without a lookup -- or when the tracker has no signal -- it falls back to
the reachability probe: any sub-500 status is HEALTHY (a 404 on the base
path or a 401 for a missing key still proves the service is up), a 5xx is
UNHEALTHY, and a connection with no ``base_url`` (litellm-routed) is
UNKNOWN.
"""

from unittest.mock import patch

import httpx
import pytest

from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.checks.llm_provider import LlmProviderHealthCheck
from synthorg.providers.health import ProviderHealthSummary

pytestmark = pytest.mark.unit


def _make_connection(base_url: str | None) -> Connection:
    return Connection(
        name="provider-example",
        connection_type=ConnectionType.LLM_PROVIDER,
        auth_method=AuthMethod.API_KEY,
        base_url=base_url,
    )


def _summary(*, calls: int, error_rate: float) -> ProviderHealthSummary:
    return ProviderHealthSummary(
        calls_last_24h=calls,
        error_rate_percent_24h=error_rate,
        avg_response_time_ms=120.0,
    )


class TestTrackerPreferredPath:
    """A bound provider-health lookup wins over any URL probe."""

    async def test_tracker_up_is_healthy_without_probe(
        self, respx_mock: object
    ) -> None:
        check = LlmProviderHealthCheck()

        async def _lookup(_name: str) -> ProviderHealthSummary:
            return _summary(calls=10, error_rate=0.0)

        check.bind_provider_health(_lookup)
        report = await check.check(_make_connection("https://api.example.com/v1"))
        assert report.status is ConnectionStatus.HEALTHY
        assert report.latency_ms == 120.0
        # The tracker verdict short-circuits: no HTTP probe fired.
        assert len(respx_mock.calls) == 0  # type: ignore[attr-defined]

    async def test_tracker_down_is_unhealthy_with_error_rate_detail(self) -> None:
        check = LlmProviderHealthCheck()

        async def _lookup(_name: str) -> ProviderHealthSummary:
            return _summary(calls=8, error_rate=90.0)

        check.bind_provider_health(_lookup)
        report = await check.check(_make_connection(None))
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        assert "error rate" in report.error_detail

    async def test_tracker_unknown_falls_back_to_unknown_without_base_url(
        self,
    ) -> None:
        """Zero recorded calls leaves the tracker silent; no URL -> UNKNOWN."""
        check = LlmProviderHealthCheck()

        async def _lookup(_name: str) -> ProviderHealthSummary:
            return _summary(calls=0, error_rate=0.0)

        check.bind_provider_health(_lookup)
        report = await check.check(_make_connection(None))
        assert report.status is ConnectionStatus.UNKNOWN

    async def test_non_provider_connection_falls_back_to_probe(
        self, respx_mock: object
    ) -> None:
        """A lookup returning ``None`` keeps the reachability probe."""
        respx_mock.get("https://api.example.com/v1").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(200),
        )
        check = LlmProviderHealthCheck()

        async def _lookup(_name: str) -> ProviderHealthSummary | None:
            return None

        check.bind_provider_health(_lookup)
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("https://api.example.com/v1"))
        assert report.status is ConnectionStatus.HEALTHY


class TestLlmProviderHealthCheck:
    async def test_no_base_url_is_unknown(self) -> None:
        """A litellm-routed provider has nothing connection-local to probe."""
        report = await LlmProviderHealthCheck().check(_make_connection(None))
        assert report.status is ConnectionStatus.UNKNOWN
        assert report.error_detail is not None

    async def test_sub_500_response_is_healthy(self, respx_mock: object) -> None:
        """A 404 on the base path still proves the endpoint is reachable."""
        respx_mock.get("https://api.example.com/v1").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(404),
        )
        check = LlmProviderHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("https://api.example.com/v1"))
        assert report.status is ConnectionStatus.HEALTHY

    async def test_5xx_response_is_unhealthy(self, respx_mock: object) -> None:
        """A 5xx is the provider itself failing."""
        respx_mock.get("https://api.example.com/v1").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(503),
        )
        check = LlmProviderHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("https://api.example.com/v1"))
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail == "HTTP 503"

    async def test_network_error_is_unhealthy(self, respx_mock: object) -> None:
        """A connect / timeout error (httpx.HTTPError) means the provider down."""
        respx_mock.get("https://api.example.com/v1").mock(  # type: ignore[attr-defined]
            side_effect=httpx.ConnectError("connection refused"),
        )
        check = LlmProviderHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("https://api.example.com/v1"))
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None

    async def test_blocked_internal_url_is_unhealthy(self, respx_mock: object) -> None:
        """SSRF policy rejects a private base_url before any HTTP call."""
        check = LlmProviderHealthCheck()
        report = await check.check(_make_connection("http://127.0.0.1/v1"))
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        assert report.error_detail.startswith("ssrf_policy_rejected:")
        assert len(respx_mock.calls) == 0  # type: ignore[attr-defined]
