"""SSRF hardening for the ntfy / Slack outbound webhook sinks.

The notification sinks POST to an operator-configured URL. These tests
pin the three-layer defence shared via
``synthorg.notifications.adapters._ssrf``:

- A synchronous construction-time check rejects an obviously-bad URL
  (non-HTTP scheme, literal loopback / private IP, ``localhost``).
- The async ``start()`` pre-flight DNS-resolves the host, rejects any
  name resolving into a blocked range, and pins the live connect to the
  validated IP so DNS rebinding cannot redirect it.
- A host on the policy ``hostname_allowlist`` bypasses the private-IP
  block for legitimately-internal receivers.
"""

from typing import Final
from unittest.mock import patch

import pytest

from synthorg.notifications.adapters._ssrf import (
    build_pinned_transport,
    resolve_outbound_target,
    validate_outbound_url_scheme,
)
from synthorg.notifications.adapters.ntfy import NtfyNotificationSink
from synthorg.notifications.adapters.slack import SlackNotificationSink
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.network_validator import DnsValidationOk, NetworkPolicy

pytestmark = pytest.mark.unit

_RESOLVE_AND_CHECK: Final[str] = "synthorg.tools.network_validator.resolve_and_check"
# A documentation-range IP (TEST-NET-3) stands in for a resolved address
# behind a patched DNS resolver: ``resolve_and_check`` is stubbed out, so
# the value never hits the blocklist, mirroring the generic_http tests.
_PUBLIC_IP: Final[str] = "203.0.113.10"
# A genuinely globally-routable literal for the construction-time check,
# which DOES run the blocklist.
_LITERAL_PUBLIC_IP: Final[str] = "8.8.8.8"


# ── validate_outbound_url_scheme (sync fast-fail) ───────────────


class TestValidateOutboundUrlScheme:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/x",
            "file:///etc/passwd",
            "gopher://example.com",
        ],
    )
    def test_rejects_non_http_scheme(self, url: str) -> None:
        with pytest.raises(ValueError, match="http or https scheme"):
            validate_outbound_url_scheme(url, "server_url")

    @pytest.mark.parametrize(
        "url",
        ["http://localhost/x", "http://127.0.0.1/x", "http://[::1]/x"],
    )
    def test_rejects_loopback(self, url: str) -> None:
        with pytest.raises(ValueError, match="loopback"):
            validate_outbound_url_scheme(url, "server_url")

    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.1.1/x",
            "http://10.0.0.1/x",
            "http://169.254.169.254/latest/meta-data/",
        ],
    )
    def test_rejects_literal_private_ip(self, url: str) -> None:
        with pytest.raises(ValueError, match="private/internal IP"):
            validate_outbound_url_scheme(url, "server_url")

    def test_accepts_public_hostname(self) -> None:
        # No raise: a public hostname passes the sync check (DNS is the
        # async pre-flight's job, not this one's).
        validate_outbound_url_scheme("https://ntfy.example.com/x", "server_url")

    def test_accepts_literal_public_ip(self) -> None:
        validate_outbound_url_scheme(f"https://{_LITERAL_PUBLIC_IP}/x", "server_url")


# ── build_pinned_transport ──────────────────────────────────────


class TestBuildPinnedTransport:
    def test_returns_pinned_transport_when_ips_present(self) -> None:
        validation = DnsValidationOk(
            hostname="ntfy.example.com",
            port=443,
            resolved_ips=(_PUBLIC_IP,),
            is_https=True,
        )
        transport = build_pinned_transport(validation)
        assert isinstance(transport, PinnedDnsTransport)

    def test_returns_none_when_no_ips(self) -> None:
        validation = DnsValidationOk(
            hostname="ntfy.example.com",
            port=443,
            resolved_ips=(),
            is_https=True,
        )
        assert build_pinned_transport(validation) is None


# ── resolve_outbound_target ─────────────────────────────────────


class TestResolveOutboundTarget:
    async def test_returns_validation_on_accept(self) -> None:
        policy = NetworkPolicy()
        with patch(_RESOLVE_AND_CHECK, return_value=(_PUBLIC_IP,)):
            result = await resolve_outbound_target(
                "https://ntfy.example.com/x",
                field="server_url",
                policy=policy,
            )
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == (_PUBLIC_IP,)

    async def test_raises_when_host_resolves_to_blocked_ip(self) -> None:
        policy = NetworkPolicy()
        # The validator returns an error string when a resolved IP is
        # blocked; the helper turns that into a ValueError.
        with (
            patch(
                _RESOLVE_AND_CHECK,
                return_value="URL host resolves to blocked private/reserved IP",
            ),
            pytest.raises(ValueError, match="rejected by SSRF policy"),
        ):
            await resolve_outbound_target(
                "https://rebind.example.com/x",
                field="server_url",
                policy=policy,
            )

    async def test_raises_when_hostname_unextractable(self) -> None:
        # The async pre-flight delegates scheme rejection to the sync
        # check; what it rejects is a URL whose host cannot be extracted
        # (no ``://``), which short-circuits before any DNS lookup.
        with pytest.raises(ValueError, match="rejected by SSRF policy"):
            await resolve_outbound_target(
                "not-a-url",
                field="server_url",
                policy=NetworkPolicy(),
            )


# ── Adapter start() integration ─────────────────────────────────


class TestNtfyStartSsrf:
    async def test_start_rejects_host_resolving_to_blocked_ip(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://rebind.example.com",
            topic="alerts",
        )
        with (
            patch(
                _RESOLVE_AND_CHECK,
                return_value="URL host resolves to blocked private/reserved IP",
            ),
            pytest.raises(ValueError, match="rejected by SSRF policy"),
        ):
            await sink.start()
        assert sink._client is None

    async def test_start_pins_connect_to_validated_ip(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="alerts",
        )
        with (
            patch(_RESOLVE_AND_CHECK, return_value=(_PUBLIC_IP,)),
            patch(
                "synthorg.notifications.adapters.ntfy.httpx.AsyncClient",
                autospec=True,
            ) as mock_cls,
        ):
            await sink.start()
        transport = mock_cls.call_args.kwargs["transport"]
        assert isinstance(transport, PinnedDnsTransport)

    async def test_start_allows_allowlisted_internal_host(self) -> None:
        sink = NtfyNotificationSink(
            server_url="http://ntfy.internal",
            topic="alerts",
            network_policy=NetworkPolicy(hostname_allowlist=("ntfy.internal",)),
        )
        with (
            patch(_RESOLVE_AND_CHECK, return_value=(_PUBLIC_IP,)),
            patch(
                "synthorg.notifications.adapters.ntfy.httpx.AsyncClient",
                autospec=True,
            ),
        ):
            await sink.start()
        assert sink._client is not None
        await sink.close()


class TestSlackStartSsrf:
    async def test_start_rejects_host_resolving_to_blocked_ip(self) -> None:
        sink = SlackNotificationSink(
            webhook_url="https://rebind.example.com/services/abc",
        )
        with (
            patch(
                _RESOLVE_AND_CHECK,
                return_value="URL host resolves to blocked private/reserved IP",
            ),
            pytest.raises(ValueError, match="rejected by SSRF policy"),
        ):
            await sink.start()
        assert sink._client is None

    async def test_start_pins_connect_to_validated_ip(self) -> None:
        sink = SlackNotificationSink(
            webhook_url="https://hooks.example.com/services/abc",
        )
        with (
            patch(_RESOLVE_AND_CHECK, return_value=(_PUBLIC_IP,)),
            patch(
                "synthorg.notifications.adapters.slack.httpx.AsyncClient",
                autospec=True,
            ) as mock_cls,
        ):
            await sink.start()
        transport = mock_cls.call_args.kwargs["transport"]
        assert isinstance(transport, PinnedDnsTransport)


# ── Construction-time rejection (both adapters) ─────────────────


class TestConstructionRejectsInternalTargets:
    def test_ntfy_rejects_literal_private_ip(self) -> None:
        with pytest.raises(ValueError, match="private/internal IP"):
            NtfyNotificationSink(server_url="http://10.0.0.1", topic="t")

    def test_slack_rejects_loopback(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            SlackNotificationSink(webhook_url="http://localhost/services/abc")
