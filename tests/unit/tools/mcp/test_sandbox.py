"""Tests for Docker sandboxing of stdio MCP servers."""

import pytest
import structlog

from synthorg.observability.events.mcp import MCP_SANDBOX_NETWORK_UNSAFE
from synthorg.tools.mcp.sandbox import MCPSandboxConfig, wrap_stdio_in_sandbox

pytestmark = pytest.mark.unit

# ``wrap_stdio_in_sandbox`` is vendor-agnostic infrastructure; a fictitious
# package + env var keep the test off any real provider's names.
_EXAMPLE_PACKAGE = "@example-org/example-mcp-server"
_EXAMPLE_ENV_VAR = "EXAMPLE_API_KEY"


def _wrap(env: dict[str, str]) -> tuple[str, list[str], dict[str, str]]:
    return wrap_stdio_in_sandbox(
        command="npx",
        args=["-y", _EXAMPLE_PACKAGE],
        env=env,
        sandbox=MCPSandboxConfig(),
    )


class TestSandboxConfig:
    def test_sandbox_on_by_default(self) -> None:
        assert MCPSandboxConfig().enabled is True
        assert MCPSandboxConfig().image == "node:22-alpine"

    def test_network_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValueError, match="network"):
            MCPSandboxConfig(network="wide-open")  # type: ignore[arg-type]

    @pytest.mark.parametrize("mode", ["bridge", "none", "host"])
    def test_network_accepts_known_modes(self, mode: str) -> None:
        assert MCPSandboxConfig(network=mode).network == mode  # type: ignore[arg-type]

    def test_host_network_warns(self) -> None:
        """``host`` defeats isolation, so selecting it is surfaced loudly."""
        with structlog.testing.capture_logs() as cap:
            MCPSandboxConfig(network="host")
        events = [e for e in cap if e.get("event") == MCP_SANDBOX_NETWORK_UNSAFE]
        assert events
        assert events[0].get("log_level") == "warning"

    def test_bridge_network_does_not_warn(self) -> None:
        with structlog.testing.capture_logs() as cap:
            MCPSandboxConfig(network="bridge")
        assert not [e for e in cap if e.get("event") == MCP_SANDBOX_NETWORK_UNSAFE]


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
        assert "--user=node" in args
        assert "--read-only" in args
        assert any(a.startswith("--pids-limit=") for a in args)
        assert any(a.startswith("--memory=") for a in args)
        assert any(a.startswith("--network=") for a in args)
        assert "--env=NPM_CONFIG_IGNORE_SCRIPTS=true" in args

    def test_npx_runtime_flags_present(self) -> None:
        """--workdir/HOME/npm-cache point at the tmpfs so npx works read-only."""
        _, args, _ = _wrap({})
        assert "--workdir=/tmp" in args
        assert "--env=HOME=/tmp" in args
        assert "--env=NPM_CONFIG_CACHE=/tmp/.npm" in args
        assert any(a.startswith("--tmpfs=/tmp") for a in args)

    def test_image_command_and_args_at_tail(self) -> None:
        _, args, _ = _wrap({})
        assert args[-4:] == [
            "node:22-alpine",
            "npx",
            "-y",
            _EXAMPLE_PACKAGE,
        ]

    def test_secret_forwarded_by_name_never_in_argv(self) -> None:
        _, args, env = _wrap({_EXAMPLE_ENV_VAR: "super-secret-value"})
        # Forwarded by name: '--env' immediately followed by the key.
        assert _EXAMPLE_ENV_VAR in args
        assert args[args.index(_EXAMPLE_ENV_VAR) - 1] == "--env"
        # The secret VALUE must never appear on the command line.
        assert all("super-secret-value" not in a for a in args)
        # It travels in the docker process env instead (Docker forwards by name).
        assert env[_EXAMPLE_ENV_VAR] == "super-secret-value"
