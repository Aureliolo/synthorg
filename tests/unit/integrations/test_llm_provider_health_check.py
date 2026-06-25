"""LlmProviderHealthCheck: lenient reachability + no-base_url handling.

An LLM endpoint that answers with any sub-500 status is reachable (a 404 on
the base path or a 401 for a missing key still proves the service is up), so
those map to HEALTHY. A 5xx is the provider failing (UNHEALTHY), and a
connection with no ``base_url`` (litellm-routed) is UNKNOWN.
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

pytestmark = pytest.mark.unit


def _make_connection(base_url: str | None) -> Connection:
    return Connection(
        name="provider-example",
        connection_type=ConnectionType.LLM_PROVIDER,
        auth_method=AuthMethod.API_KEY,
        base_url=base_url,
    )


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

    async def test_blocked_internal_url_is_unhealthy(self, respx_mock: object) -> None:
        """SSRF policy rejects a private base_url before any HTTP call."""
        check = LlmProviderHealthCheck()
        report = await check.check(_make_connection("http://127.0.0.1/v1"))
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        assert report.error_detail.startswith("ssrf_policy_rejected:")
        assert len(respx_mock.calls) == 0  # type: ignore[attr-defined]
