"""Slack health check: per-connection api_base_url override."""

from typing import cast
from unittest.mock import AsyncMock, create_autospec

import httpx
import pytest

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.checks.slack import SlackHealthCheck

pytestmark = pytest.mark.unit


def _make_connection() -> Connection:
    return Connection(
        name="acme-slack",
        connection_type=ConnectionType.SLACK,
        auth_method=AuthMethod.BEARER_TOKEN,
    )


def _make_catalog(credentials: dict[str, str]) -> ConnectionCatalog:
    """Build a typed catalog stub that returns the given credentials dict."""
    catalog = create_autospec(ConnectionCatalog, instance=True, spec_set=True)
    catalog.get_credentials = AsyncMock(return_value=credentials)
    return cast("ConnectionCatalog", catalog)


class TestSlackHealthCheckEndpoint:
    """Slack health check resolves the API base URL per-connection."""

    async def test_defaults_to_slack_com_when_api_base_url_absent(
        self,
        respx_mock: object,
    ) -> None:
        """Without an api_base_url credential, the check hits slack.com."""
        route = respx_mock.post(  # type: ignore[attr-defined]
            "https://slack.com/api/auth.test",
        ).mock(return_value=httpx.Response(200, json={"ok": True}))
        check = SlackHealthCheck(catalog=_make_catalog({"token": "xoxb-test"}))

        report = await check.check(_make_connection())

        assert report.status == ConnectionStatus.HEALTHY
        assert route.called

    async def test_enterprise_grid_override_hits_custom_url(
        self,
        respx_mock: object,
    ) -> None:
        """An https://<tenant>.slack.com override routes to that URL."""
        route = respx_mock.post(  # type: ignore[attr-defined]
            "https://acme.slack.com/api/auth.test",
        ).mock(return_value=httpx.Response(200, json={"ok": True}))
        check = SlackHealthCheck(
            catalog=_make_catalog(
                {"token": "xoxb-test", "api_base_url": "https://acme.slack.com"},
            ),
        )

        report = await check.check(_make_connection())

        assert report.status == ConnectionStatus.HEALTHY
        assert route.called

    async def test_non_slack_host_rejected(self) -> None:
        """A non-slack.com host returns UNHEALTHY without an HTTP call."""
        check = SlackHealthCheck(
            catalog=_make_catalog(
                {"token": "xoxb-test", "api_base_url": "https://evil.example.com"},
            ),
        )

        report = await check.check(_make_connection())

        assert report.status == ConnectionStatus.UNHEALTHY
        assert "invalid api_base_url" in (report.error_detail or "").lower()

    async def test_http_scheme_rejected(self) -> None:
        """An http:// override is rejected by the validator."""
        check = SlackHealthCheck(
            catalog=_make_catalog(
                {"token": "xoxb-test", "api_base_url": "http://slack.com"},
            ),
        )

        report = await check.check(_make_connection())

        assert report.status == ConnectionStatus.UNHEALTHY
        assert "invalid api_base_url" in (report.error_detail or "").lower()
