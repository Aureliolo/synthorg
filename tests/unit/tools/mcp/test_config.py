"""Tests for MCP configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestMCPServerConfigStdio:
    """Stdio transport validation."""

    def test_valid_stdio(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="node",
            args=("server.js",),
        )
        assert cfg.name == "s1"
        assert cfg.transport == "stdio"
        assert cfg.command == "node"
        assert cfg.args == ("server.js",)

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValidationError, match="requires 'command'"):
            MCPServerConfig(
                name="s1",
                transport="stdio",
            )

    def test_stdio_with_env(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="node",
            env={"NODE_ENV": "test"},
        )
        assert cfg.env == {"NODE_ENV": "test"}


class TestMCPServerConfigHTTP:
    """Streamable HTTP transport validation."""

    def test_valid_http(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="streamable_http",
            url="http://localhost:8080/mcp",
        )
        assert cfg.url == "http://localhost:8080/mcp"

    def test_http_requires_url(self) -> None:
        with pytest.raises(ValidationError, match="requires 'url'"):
            MCPServerConfig(
                name="s1",
                transport="streamable_http",
            )

    def test_http_with_headers(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="streamable_http",
            url="http://localhost:8080",
            headers={"Authorization": "Bearer test"},
        )
        assert cfg.headers == {"Authorization": "Bearer test"}


class TestMCPServerConfigToolFilters:
    """Enabled/disabled tool filter validation."""

    def test_enabled_tools_only(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="node",
            enabled_tools=("tool_a", "tool_b"),
        )
        assert cfg.enabled_tools == ("tool_a", "tool_b")

    def test_disabled_tools_only(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="node",
            disabled_tools=("tool_c",),
        )
        assert cfg.disabled_tools == ("tool_c",)

    def test_overlap_rejected(self) -> None:
        with pytest.raises(ValidationError, match="overlap"):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="node",
                enabled_tools=("tool_a", "tool_b"),
                disabled_tools=("tool_b",),
            )

    def test_no_overlap_allowed(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="node",
            enabled_tools=("tool_a",),
            disabled_tools=("tool_c",),
        )
        assert cfg.enabled_tools == ("tool_a",)
        assert cfg.disabled_tools == ("tool_c",)


class TestMCPServerConfigDefaults:
    """Default values and boundaries."""

    def test_defaults(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="echo",
        )
        assert cfg.timeout_seconds == 30.0
        assert cfg.connect_timeout_seconds == 10.0
        assert cfg.result_cache_ttl_seconds == 60.0
        assert cfg.result_cache_max_size == 256
        assert cfg.enabled is True
        assert cfg.enabled_tools is None
        assert cfg.disabled_tools == ()

    def test_timeout_bounds(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                timeout_seconds=0,
            )
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                timeout_seconds=601,
            )

    def test_frozen(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="echo",
        )
        with pytest.raises(ValidationError):
            cfg.name = "changed"  # type: ignore[misc]

    def test_invalid_transport(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="invalid",  # type: ignore[arg-type]
                command="echo",
            )


class TestMCPConfig:
    """Top-level MCP config validation."""

    def test_empty_servers(self) -> None:
        cfg = MCPConfig()
        assert cfg.servers == ()

    def test_single_server(self) -> None:
        server = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="echo",
        )
        cfg = MCPConfig(servers=(server,))
        assert len(cfg.servers) == 1

    def test_duplicate_server_names_rejected(self) -> None:
        server1 = MCPServerConfig(
            name="same",
            transport="stdio",
            command="echo",
        )
        server2 = MCPServerConfig(
            name="same",
            transport="streamable_http",
            url="http://localhost",
        )
        with pytest.raises(ValidationError, match="Duplicate"):
            MCPConfig(servers=(server1, server2))

    def test_unique_server_names_allowed(self) -> None:
        server1 = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="echo",
        )
        server2 = MCPServerConfig(
            name="s2",
            transport="streamable_http",
            url="http://localhost",
        )
        cfg = MCPConfig(servers=(server1, server2))
        assert len(cfg.servers) == 2

    def test_frozen(self) -> None:
        cfg = MCPConfig()
        with pytest.raises(ValidationError):
            cfg.servers = ()  # type: ignore[misc]


class TestMCPServerConfigBounds:
    """Additional field boundary tests."""

    def test_connect_timeout_exceeds_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                connect_timeout_seconds=121,
            )

    def test_result_cache_ttl_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                result_cache_ttl_seconds=-1,
            )

    def test_result_cache_ttl_zero_accepted(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="echo",
            result_cache_ttl_seconds=0,
        )
        assert cfg.result_cache_ttl_seconds == 0
