"""GenericHttpHealthCheck: NetworkPolicy enforcement + secret-leak scrubbing.

The generic HTTP health check is the most permissive health checker in the
catalog (any ``base_url`` is accepted at config time), so it is the highest
SSRF risk surface. These tests pin three properties:

- The health check rejects ``base_url`` values that resolve to private,
  loopback, link-local, or reserved IP ranges *before* an HTTP request is
  issued (no TOCTOU window).
- Hosts listed in the ``NetworkPolicy.hostname_allowlist`` bypass the block.
- A failing ``httpx.HTTPError`` is scrubbed via ``safe_error_description``
  before reaching the persisted ``HealthReport.error_detail`` so transport
  errors cannot leak credentials or URLs into the audit chain.
"""

from typing import Final
from unittest.mock import patch

import httpx
import pytest

from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.checks.generic_http import GenericHttpHealthCheck
from synthorg.tools.network_validator import NetworkPolicy

pytestmark = pytest.mark.unit

_BLOCKED_URLS: Final[tuple[str, ...]] = (
    "http://127.0.0.1/health",
    "http://10.0.0.1/health",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/health",
    "http://localhost/health",
)


def _make_connection(base_url: str) -> Connection:
    return Connection(
        name="generic-http-target",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url=base_url,
    )


class TestNetworkPolicyEnforcement:
    """NetworkPolicy rejects internal hosts before the HTTP call."""

    @pytest.mark.parametrize("url", _BLOCKED_URLS)
    async def test_blocked_url_returns_unhealthy_without_http_call(
        self,
        url: str,
        respx_mock: object,
    ) -> None:
        """Each blocked URL surfaces an UNHEALTHY report and no HTTP request."""
        # No respx routes registered: any HTTP call would raise.
        check = GenericHttpHealthCheck()
        report = await check.check(_make_connection(url))
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        # No HTTP call should have been made; respx_mock records zero calls.
        assert len(respx_mock.calls) == 0  # type: ignore[attr-defined]

    async def test_blocked_url_does_not_leak_validator_internals(
        self,
        respx_mock: object,
    ) -> None:
        """The ``error_detail`` carries a structured rejection, not a stacktrace."""
        check = GenericHttpHealthCheck()
        report = await check.check(_make_connection("http://127.0.0.1/health"))
        assert report.error_detail is not None
        # No traceback / no leaked DNS resolver internals.
        assert "Traceback" not in report.error_detail
        assert "asyncio" not in report.error_detail

    async def test_allowlisted_internal_host_reaches_network(
        self,
        respx_mock: object,
    ) -> None:
        """A host in ``hostname_allowlist`` bypasses the private-IP block."""
        # ``probe.internal`` is allowlisted; the validator does still
        # perform DNS resolution for IP pinning, but the result does
        # not affect the bypass. Mock the resolver path.
        respx_mock.head("http://probe.internal/health").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(200),
        )
        policy = NetworkPolicy(hostname_allowlist=("probe.internal",))
        check = GenericHttpHealthCheck(network_policy=policy)

        # The validator's ``resolve_and_check`` runs unconditionally on the
        # allowlist path. Stub it so the test does not depend on real DNS.
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("http://probe.internal/health"))
        assert report.status is ConnectionStatus.HEALTHY

    async def test_public_host_unaffected_by_default_policy(
        self,
        respx_mock: object,
    ) -> None:
        """Public hosts still reach the HTTP layer under the default policy."""
        respx_mock.head("https://example.com/").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(200),
        )
        check = GenericHttpHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("https://example.com/"))
        assert report.status is ConnectionStatus.HEALTHY


class TestSecretLeakScrubbing:
    """``httpx.HTTPError`` text is scrubbed before reaching the report."""

    async def test_url_with_userinfo_does_not_appear_in_error_detail(
        self,
        respx_mock: object,
    ) -> None:
        """Even if httpx raises with a credential-bearing message, it is scrubbed."""
        # The transport raises a ``ConnectError`` whose message embeds a
        # URL with userinfo and a known credential-style query parameter
        # (``client_secret``); both are scrubbed by
        # ``safe_error_description`` before reaching ``error_detail``.
        leaky_message = (
            "ConnectError: failed to connect to "
            "https://user:supersecret@example.com/health?client_secret=abc123"
        )
        respx_mock.head("https://example.com/health").mock(  # type: ignore[attr-defined]
            side_effect=httpx.ConnectError(leaky_message),
        )
        check = GenericHttpHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(
                _make_connection("https://example.com/health"),
            )
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        # ``safe_error_description`` scrubs URL userinfo + bearer-style
        # query string secrets. The raw secret strings must not leak.
        assert "supersecret" not in report.error_detail
        assert "abc123" not in report.error_detail
        # The scrubbed shape carries the exception class name as a prefix.
        assert report.error_detail.startswith("ConnectError:")

    async def test_error_detail_is_safe_error_description(
        self,
        respx_mock: object,
    ) -> None:
        """``error_detail`` matches the ``safe_error_description`` contract."""
        respx_mock.head("https://example.com/").mock(  # type: ignore[attr-defined]
            side_effect=httpx.ReadTimeout("timed out reading https://example.com/"),
        )
        check = GenericHttpHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_make_connection("https://example.com/"))
        assert report.error_detail is not None
        # Class-name prefix is the canonical scrubbed shape.
        assert report.error_detail.startswith("ReadTimeout:")
