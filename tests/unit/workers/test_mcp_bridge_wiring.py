"""Tests for the MCP bridge boot wiring (sandbox-config resolution)."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.mcp.sandbox import MCPSandboxConfig
from synthorg.workers._mcp_bridge_wiring import _resolve_mcp_sandbox_config
from tests._shared import mock_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit


def _app_state() -> AppState:
    return cast("AppState", SimpleNamespace())


def _patch(monkeypatch: pytest.MonkeyPatch, resolver: ConfigResolver) -> None:
    monkeypatch.setattr(
        "synthorg.workers._mcp_bridge_wiring.config_resolver_of",
        lambda _app_state: resolver,
    )


async def test_resolves_sandbox_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = False
    resolver.get_str.side_effect = lambda _ns, key: {
        "mcp_sandbox_image": "node:22-alpine",
        "mcp_sandbox_memory_limit": "256m",
        "mcp_sandbox_cpus": "0.5",
        "mcp_sandbox_network": "none",
    }[key]
    resolver.get_int.return_value = 128
    _patch(monkeypatch, cast("ConfigResolver", resolver))
    config = await _resolve_mcp_sandbox_config(_app_state())
    assert config.enabled is False
    assert config.network == "none"
    assert config.memory_limit == "256m"


async def test_fail_secure_to_sandbox_on_when_settings_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settings-resolve failure keeps sandboxing ON with secure defaults."""
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = RuntimeError("settings backend down")
    _patch(monkeypatch, cast("ConfigResolver", resolver))
    config = await _resolve_mcp_sandbox_config(_app_state())
    assert config == MCPSandboxConfig()
    assert config.enabled is True
