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

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.http_vendor import (
    HTTP_VENDOR_PRESETS,
    METADATA_KEY_VENDOR,
    HttpVendor,
    HttpVendorPreset,
)
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.integrations.health.checks.generic_http import (
    GenericHttpHealthCheck,
    _probe_target,
)
from synthorg.tools.network_validator import NetworkPolicy
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_BLOCKED_URLS: Final[tuple[tuple[str, str], ...]] = (
    ("loopback_ipv4", "http://127.0.0.1/health"),
    ("private_10", "http://10.0.0.1/health"),
    ("link_local_metadata", "http://169.254.169.254/latest/meta-data/"),
    ("loopback_ipv6", "http://[::1]/health"),
    ("localhost", "http://localhost/health"),
)


def _make_connection(base_url: str) -> Connection:
    return Connection(
        name="generic-http-target",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url=base_url,
    )


def _vendor_connection(vendor: str) -> Connection:
    """A connection bound to *vendor*, sitting at that vendor's endpoint.

    Returns:
        The connection.
    """
    preset = HTTP_VENDOR_PRESETS[HttpVendor(vendor)]
    return Connection(
        name="generic-http-target",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url=preset.base_url,
        metadata={METADATA_KEY_VENDOR: vendor},
    )


class TestNetworkPolicyEnforcement:
    """NetworkPolicy rejects internal hosts before the HTTP call."""

    @pytest.mark.parametrize(
        "url",
        [url for _, url in _BLOCKED_URLS],
        ids=[name for name, _ in _BLOCKED_URLS],
    )
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
        # SSRF rejections carry the ``ssrf_policy_rejected:`` prefix so
        # dashboards can distinguish them from generic network failures.
        assert report.error_detail.startswith("ssrf_policy_rejected:")
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

    async def test_redirect_to_internal_ip_not_followed(
        self,
        respx_mock: object,
    ) -> None:
        """A 302 redirect to ``127.0.0.1`` is not followed.

        The SSRF pre-flight validates only the initial ``base_url``;
        following redirects would bypass the gate. This pins the
        ``follow_redirects=False`` contract so a refactor that flips
        the default cannot silently regress the SSRF surface.
        """
        # The transport returns a 302 pointing at loopback. With
        # ``follow_redirects=False`` httpx surfaces the 302 as the
        # final response and the health check treats it as UNHEALTHY
        # (status >= 400 is not the gate; the < 400 check passes
        # for 3xx, so this path actually returns HEALTHY today). What
        # we pin here is the *absence* of a second call to the
        # redirect target; the bypass would manifest as a follow-up
        # request to ``127.0.0.1`` which the test asserts never fires.
        respx_mock.head("https://example.com/health").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(
                302,
                headers={"Location": "http://127.0.0.1/health"},
            ),
        )
        check = GenericHttpHealthCheck()
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            await check.check(_make_connection("https://example.com/health"))
        # Exactly one call: the HEAD to example.com. No follow-up to loopback.
        calls = respx_mock.calls  # type: ignore[attr-defined]
        assert len(calls) == 1
        assert "127.0.0.1" not in str(calls[0].request.url)


class TestAuthenticatedProbe:
    """When a catalog is bound the probe authenticates rather than false-greens."""

    async def test_sends_auth_header_when_catalog_bound(
        self,
        respx_mock: object,
    ) -> None:
        route = respx_mock.head("https://api.example.com/v1/search").mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(200),
        )
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.return_value = {
            "header_name": "X-Subscription-Token",
            "header_value": "key-123",
        }
        check = GenericHttpHealthCheck(catalog=catalog)
        conn = _make_connection("https://api.example.com/v1/search")
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(conn)
        assert report.status is ConnectionStatus.HEALTHY
        request = route.calls.last.request
        assert request.headers["X-Subscription-Token"] == "key-123"

    async def test_broken_credentials_report_unhealthy(
        self,
        respx_mock: object,
    ) -> None:
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.side_effect = SecretRetrievalError("missing secret")
        check = GenericHttpHealthCheck(catalog=catalog)
        conn = _make_connection("https://api.example.com/v1/search")
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(conn)
        assert report.status is ConnectionStatus.UNHEALTHY
        # A secret store that is down is transient and clears itself, whereas
        # a bad credential needs re-entering: reporting both the same way
        # sends the operator to rotate a key that was never the problem.
        assert report.error_detail == "secret_store_unavailable"

    async def test_a_missing_key_is_reported_as_misconfiguration(
        self,
        respx_mock: object,
    ) -> None:
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.return_value = {}
        check = GenericHttpHealthCheck(catalog=catalog)
        conn = _vendor_connection(HttpVendor.BRAVE.value)
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(conn)
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail == "credential_misconfigured"


class TestVendorPresetProbe:
    """A preset names the header its API accepts and how to read its errors."""

    async def test_probe_sends_the_vendor_header_and_buys_nothing(
        self,
        respx_mock: object,
    ) -> None:
        # The probe carries the credential and no query. Sending one would be
        # a billable search on every probe of every cycle, which is what this
        # replaced.
        preset = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]
        route = respx_mock.get(preset.base_url).mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(
                422,
                json={
                    "error": {
                        "code": "VALIDATION",
                        "meta": {"errors": [{"loc": ["query", "q"]}]},
                    }
                },
            ),
        )
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.return_value = {"token": "key-123"}
        check = GenericHttpHealthCheck(catalog=catalog)
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_vendor_connection(HttpVendor.BRAVE.value))

        # The rejection IS the pass: it proves the request reached the handler
        # with the credential accepted, and the vendor does not bill an error.
        assert report.status is ConnectionStatus.HEALTHY
        request = route.calls.last.request
        # The generic X-API-Key guess would be rejected however valid the key.
        assert request.headers["X-Subscription-Token"] == "key-123"
        assert "X-API-Key" not in request.headers
        assert "q" not in request.url.params

    async def test_a_rejected_credential_is_still_caught(
        self,
        respx_mock: object,
    ) -> None:
        # Spending nothing is worthless if it also stops noticing a dead key.
        preset = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]
        respx_mock.get(preset.base_url).mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(
                422,
                json={"error": {"code": "SUBSCRIPTION_TOKEN_INVALID"}},
            ),
        )
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.return_value = {"token": "bad-key"}
        check = GenericHttpHealthCheck(catalog=catalog)
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_vendor_connection(HttpVendor.BRAVE.value))

        assert report.status is ConnectionStatus.UNHEALTHY

    async def test_a_rate_limit_reports_its_retry_after(
        self,
        respx_mock: object,
    ) -> None:
        preset = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]
        respx_mock.get(preset.base_url).mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(429, headers={"Retry-After": "42"}),
        )
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.return_value = {"token": "key-123"}
        check = GenericHttpHealthCheck(catalog=catalog)
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_vendor_connection(HttpVendor.BRAVE.value))

        # A quota exhausted by the probe itself says nothing about the key.
        assert report.status is ConnectionStatus.UNHEALTHY
        assert "42" in (report.error_detail or "")
        # The parsed field, not just the rendered detail: the recheck floor
        # reads this one, and the probe flattens httpx's case-insensitive
        # headers into a lower-cased dict on the way here.
        assert report.retry_after_seconds == 42.0

    @pytest.mark.parametrize("advertised", ["inf", "nan", "-inf"])
    async def test_a_non_finite_retry_after_is_ignored(
        self,
        respx_mock: object,
        advertised: str,
    ) -> None:
        # ``float`` accepts these, and the value becomes a floor on the
        # recheck interval, so honouring one would let the endpoint that just
        # refused the probe retire the connection from probing for good.
        preset = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]
        respx_mock.get(preset.base_url).mock(  # type: ignore[attr-defined]
            return_value=httpx.Response(429, headers={"Retry-After": advertised}),
        )
        catalog = mock_of[ConnectionCatalog]()
        catalog.get_credentials.return_value = {"token": "key-123"}
        check = GenericHttpHealthCheck(catalog=catalog)
        with patch(
            "synthorg.tools.network_validator.resolve_and_check",
            return_value=("203.0.113.10",),
        ):
            report = await check.check(_vendor_connection(HttpVendor.BRAVE.value))

        assert report.retry_after_seconds is None

    def test_probe_target_prefers_a_free_metadata_endpoint(self) -> None:
        # A vendor that publishes a way to read its own key state is probed
        # there instead of at the endpoint that sells things.
        preset = HttpVendorPreset(
            id=NotBlankStr("probe-vendor"),
            label=NotBlankStr("Probe Vendor"),
            base_url=NotBlankStr("https://api.example.test/search"),
            auth_header=NotBlankStr("X-Key"),
            health_url="https://api.example.test/usage",
        )
        conn = _make_connection("https://api.example.test/search")
        with patch(
            "synthorg.integrations.health.checks.generic_http.resolve_vendor",
            return_value=preset,
        ):
            target = _probe_target(conn)

        assert target.url == "https://api.example.test/usage"

    def test_probe_target_falls_back_to_the_connection_url(self) -> None:
        preset = HttpVendorPreset(
            id=NotBlankStr("probe-vendor"),
            label=NotBlankStr("Probe Vendor"),
            base_url=NotBlankStr("https://api.example.test/search"),
            auth_header=NotBlankStr("X-Key"),
        )
        conn = _make_connection("https://api.example.test/search")
        with patch(
            "synthorg.integrations.health.checks.generic_http.resolve_vendor",
            return_value=preset,
        ):
            target = _probe_target(conn)

        assert target.url == "https://api.example.test/search"


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
