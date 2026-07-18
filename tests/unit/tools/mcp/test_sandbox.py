"""Tests for Docker sandboxing of stdio MCP servers."""

import pytest

from synthorg.tools.mcp.sandbox import MCPSandboxConfig, wrap_stdio_in_sandbox

pytestmark = pytest.mark.unit


def _wrap(env: dict[str, str]) -> tuple[str, list[str], dict[str, str]]:
    return wrap_stdio_in_sandbox(
        command="npx",
        args=["-y", "@brave/brave-search-mcp-server"],
        env=env,
        sandbox=MCPSandboxConfig(),
    )


class TestSandboxConfig:
    def test_sandbox_on_by_default(self) -> None:
        assert MCPSandboxConfig().enabled is True
        assert MCPSandboxConfig().image == "node:22-alpine"


class TestWrap:
    def test_runs_via_docker(self) -> None:
        command, args, _ = _wrap({})
        assert command == "docker"
        assert args[0] == "run"
        assert "--rm" in args
        assert "-i" in args

    def test_hardening_flags_present(self) -> None:
        _, args, _ = _wrap({})
        assert "--cap-drop=ALL" in args
        assert "--security-opt=no-new-privileges" in args
        assert "--read-only" in args
        assert any(a.startswith("--pids-limit=") for a in args)
        assert any(a.startswith("--memory=") for a in args)
        assert any(a.startswith("--network=") for a in args)
        assert "--env=NPM_CONFIG_IGNORE_SCRIPTS=true" in args

    def test_image_command_and_args_at_tail(self) -> None:
        _, args, _ = _wrap({})
        assert args[-4:] == [
            "node:22-alpine",
            "npx",
            "-y",
            "@brave/brave-search-mcp-server",
        ]

    def test_secret_forwarded_by_name_never_in_argv(self) -> None:
        _, args, env = _wrap({"BRAVE_API_KEY": "super-secret-value"})
        # Forwarded by name: '--env' immediately followed by the key.
        assert "BRAVE_API_KEY" in args
        assert args[args.index("BRAVE_API_KEY") - 1] == "--env"
        # The secret VALUE must never appear on the command line.
        assert all("super-secret-value" not in a for a in args)
        # It travels in the docker process env instead (Docker forwards by name).
        assert env["BRAVE_API_KEY"] == "super-secret-value"
