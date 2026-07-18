"""Tests for stdio credential injection at connect time.

The systemic bug this fixes: a connection-bound catalog server used to spawn
with only a ``SYNTHORG_CONNECTION`` name and no real secret. The client now
resolves the connection's credentials and maps them into the environment
variables / command-line flags the server expects, at connect time (never
persisted).
"""

import pytest

from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPServerConfig

pytestmark = pytest.mark.unit


class _StubCreds:
    def __init__(self, creds: dict[str, str]) -> None:
        self._creds = creds

    async def get_credentials(self, name: str) -> dict[str, str]:
        del name
        return dict(self._creds)


def _config(**overrides: object) -> MCPServerConfig:
    base: dict[str, object] = {
        "name": "srv",
        "transport": "stdio",
        "command": "npx",
        "args": ("-y", "pkg"),
    }
    base.update(overrides)
    return MCPServerConfig(**base)  # type: ignore[arg-type]


async def test_env_credentials_injected() -> None:
    config = _config(
        connection_name="gh",
        credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    )
    client = MCPClient(config, credential_source=_StubCreds({"token": "secret123"}))
    args, env = await client._resolve_stdio_launch()
    assert env is not None
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "secret123"
    assert args == ["-y", "pkg"]


async def test_arg_credentials_injected() -> None:
    config = _config(
        connection_name="db",
        credential_arg_map={"database": "--db-path"},
    )
    client = MCPClient(config, credential_source=_StubCreds({"database": "/data/x.db"}))
    args, env = await client._resolve_stdio_launch()
    assert args == ["-y", "pkg", "--db-path", "/data/x.db"]
    assert env is None


async def test_no_credential_source_leaves_server_unauthenticated() -> None:
    config = _config(
        connection_name="gh",
        credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    )
    client = MCPClient(config, credential_source=None)
    _args, env = await client._resolve_stdio_launch()
    assert env is None or "GITHUB_PERSONAL_ACCESS_TOKEN" not in env


async def test_connectionless_server_gets_no_injection() -> None:
    client = MCPClient(_config(), credential_source=_StubCreds({"token": "x"}))
    args, env = await client._resolve_stdio_launch()
    assert args == ["-y", "pkg"]
    assert env is None
