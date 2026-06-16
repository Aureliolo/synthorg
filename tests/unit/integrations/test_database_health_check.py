"""Unit tests for the database connectivity health check.

The check opens a real TCP connection to the configured host/port (a
driver-free, dialect-agnostic liveness signal) rather than falsely
reporting ``HEALTHY`` from metadata presence alone.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.checks.database import DatabaseHealthCheck
from tests._shared import FakeClock


def _connection(**metadata: str) -> Connection:
    return Connection(
        name="primary-db",
        connection_type=ConnectionType.DATABASE,
        auth_method=AuthMethod.API_KEY,
        metadata=metadata,
    )


@pytest.fixture
async def listening_port() -> AsyncIterator[int]:
    """Start a throwaway TCP server and yield its bound port."""

    async def _handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        del reader
        writer.close()

    server = await asyncio.start_server(_handle, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await server.start_serving()
        yield port


@pytest.fixture
async def refused_port() -> int:
    """An ephemeral loopback port with nothing listening on it.

    Binds port 0 to claim a free port, then closes the server so a connect
    deterministically refuses. Avoids the environment-dependent assumption
    that a hardcoded port (e.g. 1) is always closed.
    """

    async def _noop(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:  # pragma: no cover - server is closed before any connect
        del reader
        writer.close()

    server = await asyncio.start_server(_noop, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    server.close()
    await server.wait_closed()
    return port


@pytest.mark.unit
class TestDatabaseHealthCheck:
    def test_uses_injected_clock(self) -> None:
        fake = FakeClock()
        assert DatabaseHealthCheck(clock=fake)._clock is fake

    async def test_reachable_endpoint_is_healthy(self, listening_port: int) -> None:
        check = DatabaseHealthCheck()
        report = await check.check(
            _connection(host="127.0.0.1", port=str(listening_port), dialect="postgres"),
        )
        assert report.status == ConnectionStatus.HEALTHY
        assert report.error_detail is None

    async def test_unreachable_endpoint_is_unhealthy(self, refused_port: int) -> None:
        # A claimed-then-closed ephemeral port refuses connections
        # deterministically, so the probe fails fast with a connection
        # error rather than a false HEALTHY (no env-dependent hardcoded port).
        check = DatabaseHealthCheck()
        report = await check.check(
            _connection(host="127.0.0.1", port=str(refused_port)),
        )
        assert report.status == ConnectionStatus.UNHEALTHY
        assert report.error_detail is not None

    async def test_missing_host_reports_unknown(self) -> None:
        check = DatabaseHealthCheck()
        report = await check.check(_connection(port="5432"))
        assert report.status == ConnectionStatus.UNKNOWN
        assert report.error_detail is not None
        assert "host" in report.error_detail.lower()

    async def test_invalid_port_reports_unknown(self) -> None:
        check = DatabaseHealthCheck()
        report = await check.check(_connection(host="127.0.0.1", port="not-a-number"))
        assert report.status == ConnectionStatus.UNKNOWN

    async def test_port_out_of_range_reports_unknown(self) -> None:
        check = DatabaseHealthCheck()
        report = await check.check(_connection(host="127.0.0.1", port="70000"))
        assert report.status == ConnectionStatus.UNKNOWN
