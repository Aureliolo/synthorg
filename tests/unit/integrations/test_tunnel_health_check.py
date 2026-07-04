"""TunnelHealthCheck: manager-derived readiness for tunnel connections.

The checker reports the tunnel manager's availability + credential
verdict (the same source the dashboard tunnel card shows). Without a
bound lookup, or when the connection maps to no known provider, it
reports UNKNOWN instead of guessing.
"""

import pytest

from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.checks.tunnel import TunnelHealthCheck
from synthorg.integrations.tunnel.manager import (
    credential_connection_name,
    tunnel_provider_id_for_connection,
)
from synthorg.integrations.tunnel.protocol import (
    TunnelCredentialKind,
    TunnelProviderStatus,
)

pytestmark = pytest.mark.unit


def _make_connection(name: str = "tunnel-ngrok") -> Connection:
    return Connection(
        name=name,
        connection_type=ConnectionType.TUNNEL,
        auth_method=AuthMethod.API_KEY,
    )


def _status(
    *,
    available: bool,
    credential_configured: bool,
    detail: str | None = None,
) -> TunnelProviderStatus:
    return TunnelProviderStatus(
        provider_id="ngrok",
        display_name="ngrok",
        credential_kind=TunnelCredentialKind.TOKEN,
        available=available,
        detail=detail,
        credential_configured=credential_configured,
    )


class TestTunnelHealthCheck:
    async def test_unbound_lookup_reports_unknown(self) -> None:
        report = await TunnelHealthCheck().check(_make_connection())
        assert report.status is ConnectionStatus.UNKNOWN
        assert report.error_detail == "tunnel manager not bound"

    async def test_available_with_credential_is_healthy(self) -> None:
        check = TunnelHealthCheck()

        async def _lookup(_name: str) -> TunnelProviderStatus:
            return _status(available=True, credential_configured=True)

        check.bind_tunnel_status_lookup(_lookup)
        report = await check.check(_make_connection())
        assert report.status is ConnectionStatus.HEALTHY

    async def test_unavailable_provider_is_unhealthy_with_detail(self) -> None:
        check = TunnelHealthCheck()

        async def _lookup(_name: str) -> TunnelProviderStatus:
            return _status(
                available=False,
                credential_configured=True,
                detail="binary missing and downloads disabled",
            )

        check.bind_tunnel_status_lookup(_lookup)
        report = await check.check(_make_connection())
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail == "binary missing and downloads disabled"

    async def test_missing_credential_is_unhealthy(self) -> None:
        check = TunnelHealthCheck()

        async def _lookup(_name: str) -> TunnelProviderStatus:
            return _status(available=True, credential_configured=False)

        check.bind_tunnel_status_lookup(_lookup)
        report = await check.check(_make_connection())
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        assert "credential" in report.error_detail

    async def test_unknown_provider_reports_unknown(self) -> None:
        check = TunnelHealthCheck()

        async def _lookup(_name: str) -> None:
            return None

        check.bind_tunnel_status_lookup(_lookup)
        report = await check.check(_make_connection("tunnel-nonexistent"))
        assert report.status is ConnectionStatus.UNKNOWN
        assert report.error_detail == "unknown tunnel provider"

    async def test_lookup_failure_is_unhealthy_not_raised(self) -> None:
        """The protocol forbids raising: a lookup blow-up degrades."""
        check = TunnelHealthCheck()

        async def _lookup(_name: str) -> TunnelProviderStatus:
            msg = "catalog exploded"
            raise RuntimeError(msg)

        check.bind_tunnel_status_lookup(_lookup)
        report = await check.check(_make_connection())
        assert report.status is ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None
        assert report.error_detail.startswith("tunnel status lookup failed")


class TestConnectionNameRoundTrip:
    def test_provider_id_round_trips_through_connection_name(self) -> None:
        assert (
            tunnel_provider_id_for_connection(credential_connection_name("ngrok"))
            == "ngrok"
        )

    def test_non_tunnel_name_maps_to_none(self) -> None:
        assert tunnel_provider_id_for_connection("provider-example") is None
