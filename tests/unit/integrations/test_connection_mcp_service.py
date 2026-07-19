"""Unit tests for :class:`ConnectionService` live-probe health check.

The MCP ``connections.check_health`` tool must run an on-demand probe
(mirroring the REST ``/connections/{name}/health`` path) and persist the
fresh status, rather than returning the last-known cached snapshot that
the 5-minute background prober may not have refreshed yet.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.mcp_service import ConnectionService
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
    SecretRef,
)
from synthorg.integrations.errors import ConnectionNotFoundError
from synthorg.integrations.health.models import HealthReport
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_PROBE = "synthorg.integrations.connections.mcp_service.check_connection_health"


def _connection(status: ConnectionStatus = ConnectionStatus.UNKNOWN) -> Connection:
    return Connection(
        name=NotBlankStr("c1"),
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        secret_refs=(
            SecretRef(secret_id=NotBlankStr("s-1"), backend=NotBlankStr("memory")),
        ),
        health=ConnectionHealth(status=status),
    )


async def test_check_health_runs_live_probe_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present connection is probed live, health persisted, refreshed conn back."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    refreshed = _connection(status=ConnectionStatus.HEALTHY)
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(return_value=refreshed),
        update_health=AsyncMock(),
    )
    report = HealthReport(
        connection_name=NotBlankStr("c1"),
        status=ConnectionStatus.HEALTHY,
        checked_at=now,
    )
    probe = AsyncMock(return_value=report)
    monkeypatch.setattr(_PROBE, probe)

    result = await ConnectionService(catalog=catalog).check_health(
        name=NotBlankStr("c1"),
    )

    probe.assert_awaited_once_with(catalog, "c1")
    catalog.update_health.assert_awaited_once()
    kwargs = catalog.update_health.await_args.kwargs
    assert kwargs["status"] is ConnectionStatus.HEALTHY
    assert kwargs["checked_at"] == now
    assert result is refreshed


async def test_check_health_missing_connection_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing connection returns ``None`` without probing or writing health."""
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(return_value=None),
        update_health=AsyncMock(),
    )
    probe = AsyncMock()
    monkeypatch.setattr(_PROBE, probe)

    result = await ConnectionService(catalog=catalog).check_health(
        name=NotBlankStr("missing"),
    )

    assert result is None
    probe.assert_not_awaited()
    catalog.update_health.assert_not_awaited()


async def test_check_health_concurrent_delete_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delete racing between the existence check and the probe yields ``None``."""
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(return_value=_connection()),
        update_health=AsyncMock(),
    )
    probe = AsyncMock(side_effect=ConnectionNotFoundError("gone"))
    monkeypatch.setattr(_PROBE, probe)

    result = await ConnectionService(catalog=catalog).check_health(
        name=NotBlankStr("c1"),
    )

    assert result is None
    catalog.update_health.assert_not_awaited()
