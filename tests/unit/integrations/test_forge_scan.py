"""Unit tests for the forge accessible-repo scan (repo-scope selection)."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.forge_scan import scan_accessible_repos
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.integrations.errors import ConnectionNotFoundError
from synthorg.tools.forge.errors import ForgeUnsupportedError
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_FJ = "https://code.example.com/api/v1"


def _catalog(
    conn: Connection | None,
    *,
    token: str = "t0ken",  # noqa: S107 -- test fixture token
) -> ConnectionCatalog:
    return mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=AsyncMock(
            spec=ConnectionCatalog.get_credentials, return_value={"token": token}
        ),
    )


def _forge_conn() -> Connection:
    return Connection(
        name="forge",
        connection_type=ConnectionType.FORGEJO,
        auth_method=AuthMethod.BEARER_TOKEN,
        base_url="https://code.example.com",
    )


class TestForgeScan:
    @respx.mock
    async def test_scan_lists_accessible_repos(self) -> None:
        respx.get(f"{_FJ}/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "full_name": "acme/proj-1",
                        "private": True,
                        "permissions": {"admin": True, "push": True, "pull": True},
                    }
                ],
            ),
        )
        repos = await scan_accessible_repos(_catalog(_forge_conn()), "forge")
        assert len(repos) == 1
        assert str(repos[0].owner) == "acme"
        assert repos[0].permission == "admin"

    async def test_missing_connection_raises(self) -> None:
        with pytest.raises(ConnectionNotFoundError):
            await scan_accessible_repos(_catalog(None), "nope")

    async def test_non_forge_connection_rejected(self) -> None:
        conn = Connection(
            name="chat",
            connection_type=ConnectionType.SLACK,
            auth_method=AuthMethod.BEARER_TOKEN,
            base_url="https://slack.example.com",
        )
        with pytest.raises(ForgeUnsupportedError):
            await scan_accessible_repos(_catalog(conn), "chat")
