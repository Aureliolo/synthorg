"""``HealthReport.webhook_ingest`` reports whether ingest can authenticate.

The condition it reports on is invisible everywhere else: a delivery to a
connection with no signing secret is rejected before any receipt is written, so
without this field a misconfigured inbound webhook leaves nothing but a server
log.
"""

from typing import cast

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
    WebhookIngestState,
)
from synthorg.integrations.health.service import check_connection_health
from tests._shared import mock_of

pytestmark = pytest.mark.unit

# The vendor value that keeps ``generic_http``'s signing-secret field visible;
# any preset vendor hides it, which retires the inbound path.
_CUSTOM_VENDOR = "custom"


def _connection(
    connection_type: ConnectionType,
    metadata: dict[str, str] | None = None,
) -> Connection:
    return Connection(
        name=NotBlankStr("c1"),
        connection_type=connection_type,
        auth_method=AuthMethod.API_KEY,
        metadata=metadata or {},
    )


def _catalog(conn: Connection, credentials: dict[str, str]) -> ConnectionCatalog:
    async def _get_or_raise(_name: str) -> Connection:
        return conn

    async def _get_credentials(_name: str) -> dict[str, str]:
        return credentials

    return cast(
        ConnectionCatalog,
        mock_of[ConnectionCatalog](
            get_or_raise=_get_or_raise,
            get_credentials=_get_credentials,
        ),
    )


async def test_a_stored_secret_reads_ready() -> None:
    conn = _connection(ConnectionType.GENERIC_HTTP, {"vendor": _CUSTOM_VENDOR})
    report = await check_connection_health(
        _catalog(conn, {"signing_secret": "0123456789abcdef"}),
        "c1",
    )
    assert report.webhook_ingest is WebhookIngestState.READY


async def test_an_unset_secret_reads_unconfigured() -> None:
    conn = _connection(ConnectionType.GENERIC_HTTP, {"vendor": _CUSTOM_VENDOR})
    report = await check_connection_health(_catalog(conn, {}), "c1")
    assert report.webhook_ingest is WebhookIngestState.UNCONFIGURED


async def test_a_blank_secret_reads_unconfigured() -> None:
    # Whitespace is not a secret, and ingest strips before its own emptiness
    # test; reporting READY here would contradict the 401 the sender gets.
    conn = _connection(ConnectionType.GENERIC_HTTP, {"vendor": _CUSTOM_VENDOR})
    catalog = _catalog(conn, {"signing_secret": "   "})
    report = await check_connection_health(catalog, "c1")
    assert report.webhook_ingest is WebhookIngestState.UNCONFIGURED


async def test_a_type_with_no_signing_field_reads_not_applicable() -> None:
    conn = _connection(ConnectionType.DATABASE)
    report = await check_connection_health(_catalog(conn, {}), "c1")
    assert report.webhook_ingest is WebhookIngestState.NOT_APPLICABLE


async def test_a_hidden_signing_field_reads_not_applicable() -> None:
    # A ``generic_http`` connection pointed at an outbound vendor preset can
    # never be sent a webhook, so a stored secret does not make it ingest-ready.
    conn = _connection(ConnectionType.GENERIC_HTTP, {"vendor": "example-preset"})
    report = await check_connection_health(
        _catalog(conn, {"signing_secret": "0123456789abcdef"}),
        "c1",
    )
    assert report.webhook_ingest is WebhookIngestState.NOT_APPLICABLE
