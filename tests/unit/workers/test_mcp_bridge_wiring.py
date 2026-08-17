"""Tests for the MCP bridge boot wiring (sandbox-config resolution)."""

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.mcp.sandbox import MCPSandboxConfig
from synthorg.tools.sandbox.deployment_identity import deployment_id_for
from synthorg.workers._mcp_bridge_wiring import _resolve_mcp_sandbox_config
from tests._shared import mock_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit


def _app_state(runtime: str | None = None) -> AppState:
    # The container runtime is the one value read straight off app state
    # rather than through the resolver, so the stub has to carry the whole
    # path down to it.
    return cast(
        "AppState",
        SimpleNamespace(
            config=SimpleNamespace(
                sandboxing=SimpleNamespace(docker=SimpleNamespace(runtime=runtime)),
            ),
        ),
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    resolver: ConfigResolver,
    workspace: Path,
) -> None:
    monkeypatch.setattr(
        "synthorg.workers._mcp_bridge_wiring.config_resolver_of",
        lambda _app_state: resolver,
    )
    monkeypatch.setattr(
        "synthorg.workers._mcp_bridge_wiring.agent_workspace_root_of",
        lambda _app_state: workspace,
    )


async def test_resolves_sandbox_from_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = False
    resolver.get_str.side_effect = lambda _ns, key: {
        "mcp_sandbox_memory_limit": "256m",
        "mcp_sandbox_cpus": "0.5",
        "mcp_sandbox_network": "none",
    }[key]
    resolver.get_int.return_value = 128
    _patch(monkeypatch, cast("ConfigResolver", resolver), tmp_path)
    config = await _resolve_mcp_sandbox_config(_app_state("runsc"))
    assert config.enabled is False
    assert config.network == "none"
    assert config.memory_limit == "256m"
    assert config.runtime == "runsc"


async def test_fail_secure_to_sandbox_on_when_settings_raise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A settings-resolve failure keeps sandboxing ON with secure defaults."""
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = RuntimeError("settings backend down")
    _patch(monkeypatch, cast("ConfigResolver", resolver), tmp_path)
    config = await _resolve_mcp_sandbox_config(_app_state())
    assert config.enabled is True
    assert config.memory_limit == MCPSandboxConfig().memory_limit
    assert config.network == MCPSandboxConfig().network


async def test_the_container_is_attributed_even_when_settings_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Attribution is what lets the boot pass reclaim an orphaned runtime.

    It is derived from the workspace root rather than resolved, so a settings
    backend that is down cannot cost a container its only claim to an owner.
    """
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = RuntimeError("settings backend down")
    _patch(monkeypatch, cast("ConfigResolver", resolver), tmp_path)
    config = await _resolve_mcp_sandbox_config(_app_state())
    assert config.deployment_id == deployment_id_for(tmp_path)


async def test_an_app_state_missing_its_config_still_yields_secure_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The derivations cannot escape, because the caller does not catch them.

    ``build_mcp_bridge_tools`` calls this helper outside its own handler, so
    a raise here poisons boot rather than degrading to no bridge tools. An
    unattributed container is the recoverable outcome; a backend that cannot
    finish starting is not.
    """
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = True
    resolver.get_str.side_effect = lambda _ns, key: {
        "mcp_sandbox_memory_limit": "256m",
        "mcp_sandbox_cpus": "0.5",
        "mcp_sandbox_network": "none",
    }[key]
    resolver.get_int.return_value = 128
    _patch(monkeypatch, cast("ConfigResolver", resolver), tmp_path)
    config = await _resolve_mcp_sandbox_config(cast("AppState", SimpleNamespace()))
    assert config.enabled is True
    assert config.runtime is None


async def test_an_unwired_resolver_falls_back_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unwired settings are the ordinary state before persistence connects."""

    def _unwired(_app_state: object) -> ConfigResolver:
        msg = "settings resolver not wired"
        raise ServiceUnavailableError(msg)

    monkeypatch.setattr(
        "synthorg.workers._mcp_bridge_wiring.config_resolver_of",
        _unwired,
    )
    monkeypatch.setattr(
        "synthorg.workers._mcp_bridge_wiring.agent_workspace_root_of",
        lambda _app_state: tmp_path,
    )
    config = await _resolve_mcp_sandbox_config(_app_state("runsc"))
    assert config.enabled is True
    assert config.runtime == "runsc"
    assert config.deployment_id == deployment_id_for(tmp_path)


async def test_the_agent_sandbox_runtime_survives_a_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The stronger isolation is exactly what must not be lost on the way down.

    An operator installs gVisor to contain code they distrust, and this path
    runs code nobody reviewed at all. Falling back to the daemon default here
    would hand the weaker isolation to the stronger threat, silently.
    """
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.side_effect = RuntimeError("settings backend down")
    _patch(monkeypatch, cast("ConfigResolver", resolver), tmp_path)
    config = await _resolve_mcp_sandbox_config(_app_state("runsc"))
    assert config.enabled is True
    assert config.runtime == "runsc"
