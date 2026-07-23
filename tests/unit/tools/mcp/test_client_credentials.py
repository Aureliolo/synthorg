"""Tests for stdio credential injection at connect time.

A connection-bound server resolves the bound connection's credentials and maps
them into the environment variables the server expects at connect time. Secrets
are forwarded by environment variable only, never on the argv, and are never
persisted into the stored config.
"""

import pytest
import structlog

from synthorg.observability.events.mcp import (
    MCP_CREDENTIAL_SOURCE_MISSING,
    MCP_CREDENTIALS_INJECTED,
)
from synthorg.tools.mcp.client import MCPClient
from synthorg.tools.mcp.config import MCPServerConfig
from synthorg.tools.mcp.errors import MCPConnectionError
from synthorg.tools.mcp.stdio_credentials import resolve_stdio_launch

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
        "args": ("-y", "pkg@1.0.0"),
    }
    base.update(overrides)
    return MCPServerConfig(**base)  # type: ignore[arg-type]


async def test_env_credentials_injected() -> None:
    config = _config(
        connection_name="gh",
        credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    )
    client = MCPClient(config, credential_source=_StubCreds({"token": "secret123"}))
    args, env = await resolve_stdio_launch(client._config, client._credential_source)
    assert env is not None
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "secret123"
    # The secret is forwarded by env var only; it never lands on the argv.
    assert args == ["-y", "pkg@1.0.0"]


async def test_secret_never_appears_in_args() -> None:
    config = _config(
        connection_name="gh",
        credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    )
    client = MCPClient(config, credential_source=_StubCreds({"token": "secret123"}))
    args, _env = await resolve_stdio_launch(client._config, client._credential_source)
    assert "secret123" not in args


async def test_no_credential_source_leaves_server_unauthenticated() -> None:
    config = _config(
        connection_name="gh",
        credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    )
    client = MCPClient(config, credential_source=None)
    _args, env = await resolve_stdio_launch(client._config, client._credential_source)
    assert env is None or "GITHUB_PERSONAL_ACCESS_TOKEN" not in env


async def test_connectionless_server_gets_no_injection() -> None:
    client = MCPClient(_config(), credential_source=_StubCreds({"token": "x"}))
    args, env = await resolve_stdio_launch(client._config, client._credential_source)
    assert args == ["-y", "pkg@1.0.0"]
    assert env is None


async def test_bound_connection_with_empty_map_warns() -> None:
    """A bound connection with no credential map must not fail silently."""
    config = _config(connection_name="gh")  # no credential_env_map
    client = MCPClient(config, credential_source=_StubCreds({"token": "x"}))
    with structlog.testing.capture_logs() as cap:
        _args, env = await resolve_stdio_launch(
            client._config, client._credential_source
        )
    assert env is None
    events = [e for e in cap if e.get("event") == MCP_CREDENTIAL_SOURCE_MISSING]
    assert events
    assert events[0].get("log_level") == "warning"


async def test_spawn_boundary_rescreens_mutated_target() -> None:
    """A post-construction in-place map edit fails closed at the spawn boundary.

    ``frozen=True`` blocks field reassignment but not nested mutation of the
    dict, so the spawn boundary re-screens each target before injection.
    """
    config = _config(
        connection_name="gh",
        credential_env_map={"token": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    )
    config.credential_env_map["token"] = "LD_PRELOAD"
    client = MCPClient(config, credential_source=_StubCreds({"token": "secret123"}))
    with pytest.raises(MCPConnectionError):
        await resolve_stdio_launch(client._config, client._credential_source)


async def test_partial_injection_warns() -> None:
    """A resolved connection missing a declared field is surfaced at WARNING."""
    config = _config(
        connection_name="db",
        credential_env_map={"host": "PGHOST", "password": "PGPASSWORD"},
    )
    # Only ``host`` resolves; ``password`` is absent (schema mismatch).
    client = MCPClient(config, credential_source=_StubCreds({"host": "db.internal"}))
    with structlog.testing.capture_logs() as cap:
        _args, env = await resolve_stdio_launch(
            client._config, client._credential_source
        )
    assert env is not None
    assert env["PGHOST"] == "db.internal"
    assert "PGPASSWORD" not in env
    injected = [e for e in cap if e.get("event") == MCP_CREDENTIALS_INJECTED]
    assert injected
    assert injected[0].get("log_level") == "warning"
    assert injected[0].get("expected_fields") == 2
    assert injected[0].get("injected_fields") == 1
