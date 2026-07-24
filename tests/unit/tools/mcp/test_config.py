"""Tests for MCP configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig

pytestmark = pytest.mark.unit


class TestMCPServerConfigNpmPin:
    """npm version-pin enforcement for npx-launched stdio servers."""

    def test_unpinned_npx_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="npx",
                args=("-y", "@scope/pkg"),
            )

    def test_latest_tag_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="npx",
                args=("-y", "@scope/pkg@latest"),
            )

    def test_pinned_scoped_accepted(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="npx",
            args=("-y", "@scope/pkg@2.1.0"),
        )
        assert cfg.args == ("-y", "@scope/pkg@2.1.0")

    def test_pinned_unscoped_accepted(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="npx",
            args=("pkg@1.2.3",),
        )
        assert cfg.args == ("pkg@1.2.3",)

    def test_non_npx_command_exempt(self) -> None:
        # A node/python server is not an on-the-fly npm resolve; no pin needed.
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="node",
            args=("server.js",),
        )
        assert cfg.command == "node"

    @pytest.mark.parametrize(
        "command",
        ["NPX", "Npx", "NPX.CMD", "PNPM", "BUNX", "/usr/local/bin/NPX"],
        ids=["upper", "mixed", "cmd_upper", "pnpm_upper", "bunx_upper", "path_upper"],
    )
    def test_case_varied_launcher_still_pinned(self, command: str) -> None:
        # Windows resolves NPX/PNPM/BUNX to the same launcher, so a
        # case-varied command must not slip past the pin check.
        args = ("dlx", "pkg") if command.lower().endswith("pnpm") else ("pkg",)
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(name="s1", transport="stdio", command=command, args=args)

    def test_pnpm_dlx_unpinned_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="pnpm",
                args=("dlx", "@scope/pkg"),
            )

    def test_pnpm_dlx_pinned_accepted(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="pnpm",
            args=("dlx", "@scope/pkg@1.0.0"),
        )
        assert cfg.args == ("dlx", "@scope/pkg@1.0.0")

    def test_bare_pnpm_is_exempt(self) -> None:
        # `pnpm` without `dlx` is a normal script runner, not an on-the-fly
        # resolve; nothing to pin.
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="pnpm",
            args=("run", "server"),
        )
        assert cfg.command == "pnpm"

    def test_bunx_unpinned_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(name="s1", transport="stdio", command="bunx", args=("pkg",))

    def test_windows_npx_cmd_unpinned_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(
                name="s1", transport="stdio", command="npx.cmd", args=("pkg",)
            )

    @pytest.mark.parametrize("tag", ["next", "canary", ""])
    def test_other_floating_tags_rejected(self, tag: str) -> None:
        spec = f"pkg@{tag}" if tag else "pkg@"
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(
                name="s1", transport="stdio", command="npx", args=("-y", spec)
            )

    @pytest.mark.parametrize(
        "args",
        [
            ("--package", "pkg", "bin"),
            ("-p", "pkg", "bin"),
            ("--package=pkg", "bin"),
        ],
        ids=["long", "short", "inline"],
    )
    def test_package_option_is_the_pinned_spec(self, args: tuple[str, ...]) -> None:
        # ``npx --package <pkg> <bin>`` resolves the PACKAGE, not the binary
        # name that follows it, so the pin must be read off the option value.
        with pytest.raises(ValidationError, match="must be pinned"):
            MCPServerConfig(name="s1", transport="stdio", command="npx", args=args)

    @pytest.mark.parametrize(
        "args",
        [
            ("--package", "pkg@1.2.3", "bin"),
            ("-p", "pkg@1.2.3", "bin"),
            ("--package=pkg@1.2.3", "bin"),
        ],
        ids=["long", "short", "inline"],
    )
    def test_pinned_package_option_accepted(self, args: tuple[str, ...]) -> None:
        # The unpinned trailing binary name must not be mistaken for a package.
        cfg = MCPServerConfig(name="s1", transport="stdio", command="npx", args=args)
        assert cfg.args == args


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

    @pytest.mark.parametrize(
        "timeout_seconds",
        [0, 601],
        ids=["below_min", "above_max"],
    )
    def test_timeout_bounds(self, timeout_seconds: int) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                timeout_seconds=timeout_seconds,
            )

    def test_credential_binding_rejected_on_non_stdio(self) -> None:
        """Credential binding on streamable_http is silently ineffective -> reject."""
        with pytest.raises(ValidationError, match="stdio"):
            MCPServerConfig(
                name="s1",
                transport="streamable_http",
                url="https://mcp.example/rpc",
                connection_name="bound",
                credential_env_map={"token": "TOKEN"},
            )

    def test_credential_binding_allowed_on_stdio(self) -> None:
        cfg = MCPServerConfig(
            name="s1",
            transport="stdio",
            command="echo",
            connection_name="bound",
            credential_env_map={"token": "TOKEN"},
        )
        assert cfg.connection_name == "bound"

    def test_credential_map_without_connection_rejected(self) -> None:
        """A credential map with no bound connection has nothing to resolve."""
        with pytest.raises(ValidationError, match="connection_name"):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                credential_env_map={"token": "TOKEN"},
            )

    @pytest.mark.parametrize("env_var", ["LD_PRELOAD", "NODE_OPTIONS", "PATH", "a=b"])
    def test_dangerous_credential_env_var_rejected(self, env_var: str) -> None:
        """A credential must not be injected under a process-control env var."""
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="s1",
                transport="stdio",
                command="echo",
                connection_name="bound",
                credential_env_map={"token": env_var},
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
