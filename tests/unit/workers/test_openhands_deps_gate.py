"""The OpenHands loop's boot gate, exercised through the real function.

``build_openhands_loop_deps_or_none`` is what decides whether a deployment
gets the loop at all, and what egress the container is pinned to. Testing
only its pure ``_missing_pieces`` helper leaves the actual gating boolean and
the sandbox it builds unguarded, so these drive the function itself.
"""

import functools
from pathlib import Path
from typing import cast

import pytest

from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.engine.openhands.config import OpenHandsLoopDeps
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.workers._openhands_wiring import (
    _HOST_GATEWAY_ALIAS,
    build_openhands_loop_deps_or_none,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_GATEWAY_URL = "http://host.docker.internal:3001/api/v1/gateway/v1"
_MCP_URL = "http://host.docker.internal:3001/api/v1/mcp-gateway"

_WIRED: dict[str, str] = {
    "tools.openhands_enabled": "true",
    "tools.openhands_image": "example.invalid/openhands:test",
    "tools.openhands_idle_timeout_seconds": "120",
    "tools.openhands_max_runtime_seconds": "600",
    "tools.credentialed_mcp_base_url": _MCP_URL,
    "providers.gateway_base_url": _GATEWAY_URL,
    "providers.gateway_token_ttl_seconds": "3600",
}


def _app_state(overrides: dict[str, str] | None = None, *, signer: object) -> AppState:
    values = {**_WIRED, **(overrides or {})}

    async def _get_str(namespace: str, key: str) -> str:
        return values.get(f"{namespace}.{key}", "")

    async def _get_bool(namespace: str, key: str) -> bool:
        return values.get(f"{namespace}.{key}", "").lower() == "true"

    async def _get_float(namespace: str, key: str) -> float:
        return float(values.get(f"{namespace}.{key}", "0"))

    async def _get_int(namespace: str, key: str) -> int:
        return int(values.get(f"{namespace}.{key}", "0"))

    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_str=_get_str,
        get_bool=_get_bool,
        get_float=_get_float,
        get_int=_get_int,
    )
    return make_app_state(
        config=RootConfig(company_name="test"),
        slices={
            SettingsStateSlice: {"config_resolver": resolver},
            GatewayStateSlice: {"signer": signer},
        },
    )


def _signer() -> GatewaySigner:
    return GatewaySigner(secret=b"k" * 48)


class TestGate:
    async def test_wires_when_every_piece_is_present(self) -> None:
        deps = await build_openhands_loop_deps_or_none(_app_state(signer=_signer()))

        assert deps is not None
        assert deps.gateway_base_url == _GATEWAY_URL
        assert deps.mcp_base_url == _MCP_URL

    async def test_master_off_returns_none(self) -> None:
        # The capability master must gate on its own, with every other piece
        # wired: a dropped `not enabled` term would leave this the only proof.
        deps = await build_openhands_loop_deps_or_none(
            _app_state({"tools.openhands_enabled": "false"}, signer=_signer())
        )

        assert deps is None

    async def test_absent_signer_returns_none(self) -> None:
        deps = await build_openhands_loop_deps_or_none(_app_state(signer=None))

        assert deps is None

    @pytest.mark.parametrize(
        "key",
        ["providers.gateway_base_url", "tools.credentialed_mcp_base_url"],
    )
    async def test_blank_endpoint_returns_none(self, key: str) -> None:
        deps = await build_openhands_loop_deps_or_none(
            _app_state({key: ""}, signer=_signer())
        )

        assert deps is None

    @pytest.mark.parametrize(
        "key",
        ["providers.gateway_base_url", "tools.credentialed_mcp_base_url"],
    )
    async def test_endpoint_without_a_host_returns_none(self, key: str) -> None:
        # A scheme-less URL is non-empty but parses to no host, which would
        # collapse the egress allowlist and leave the sandbox unpinned.
        deps = await build_openhands_loop_deps_or_none(
            _app_state({key: "host.docker.internal:3001/api"}, signer=_signer())
        )

        assert deps is None

    @pytest.mark.parametrize(
        "key",
        ["providers.gateway_base_url", "tools.credentialed_mcp_base_url"],
    )
    @pytest.mark.parametrize("url", ["http://host:3001", "http://host:3001/"])
    async def test_endpoint_without_a_path_returns_none(
        self, key: str, url: str
    ) -> None:
        # Both endpoints share one host, so the narrowing is per path. An
        # endpoint with no path contributes no rule, which would leave it
        # inside the host allowlist but refused per request: unreachable at
        # run time rather than reported at wiring time.
        deps = await build_openhands_loop_deps_or_none(
            _app_state({key: url}, signer=_signer())
        )

        assert deps is None

    async def test_runtime_cap_at_or_above_token_ttl_returns_none(self) -> None:
        # A run outliving its bearer fails mid-run with a 401 that reads as an
        # auth fault, so the pair is refused at wiring time instead.
        deps = await build_openhands_loop_deps_or_none(
            _app_state(
                {
                    "tools.openhands_max_runtime_seconds": "3600",
                    "providers.gateway_token_ttl_seconds": "3600",
                },
                signer=_signer(),
            )
        )

        assert deps is None


class TestSignerRotationRace:
    async def test_signer_lost_mid_wiring_returns_none(self) -> None:
        # The gateway can rebuild while this function is awaiting settings.
        # Handing the loop the signer captured before that window would mint
        # bearers the gateway no longer verifies, so the signer is re-read
        # after the awaits and an absent one fails closed.
        app_state = _app_state(signer=_signer())

        async def _get_int_then_rotate(namespace: str, key: str) -> int:
            # Fires after the availability gate, before the deps are built.
            app_state.swap_slice(GatewayStateSlice(signer=None))
            return int(_WIRED.get(f"{namespace}.{key}", "0"))

        resolver = app_state.slice(SettingsStateSlice).config_resolver
        assert resolver is not None
        object.__setattr__(resolver, "get_int", _get_int_then_rotate)

        assert await build_openhands_loop_deps_or_none(app_state) is None


def _resolved(path: Path) -> Path:
    """Resolve *path* outside an async frame, where the linter allows the call.

    Returns:
        The resolved path.
    """
    return path.resolve()


def _bound_sandbox(deps: OpenHandsLoopDeps) -> DockerSandbox:
    """Reach the sandbox the conversation factory was partial'd over.

    Returns:
        The :class:`DockerSandbox` that actually reaches Docker and the sidecar.
    """
    factory = cast("functools.partial[object]", deps.build_conversation)
    return cast("DockerSandbox", factory.args[0])


class TestWorkspaceRoot:
    async def test_defaults_to_the_deployment_agent_workspace(self) -> None:
        app_state = _app_state(signer=_signer())

        deps = await build_openhands_loop_deps_or_none(app_state)

        assert deps is not None
        assert _bound_sandbox(deps).workspace == agent_workspace_root_of(app_state)

    async def test_override_binds_the_supplied_root(self, tmp_path: Path) -> None:
        # The A/B harness runs each cell against its own recreated workspace, so
        # it supplies the root per run rather than standing up a second sandbox
        # builder that would have to re-derive the egress pin.
        deps = await build_openhands_loop_deps_or_none(
            _app_state(signer=_signer()), workspace_root=tmp_path
        )

        assert deps is not None
        assert _bound_sandbox(deps).workspace == _resolved(tmp_path)

    async def test_override_keeps_the_egress_pin(self, tmp_path: Path) -> None:
        # A per-run root must not become a second, unpinned wiring path.
        deps = await build_openhands_loop_deps_or_none(
            _app_state(signer=_signer()), workspace_root=tmp_path
        )

        assert deps is not None
        assert _bound_sandbox(deps).config.allowed_hosts == (
            "host.docker.internal:3001",
        )


class TestSandboxEgress:
    async def test_sandbox_carries_the_alias_and_both_layers_of_pinning(
        self,
    ) -> None:
        deps = await build_openhands_loop_deps_or_none(_app_state(signer=_signer()))

        assert deps is not None
        # build_conversation is a partial over the sandbox; its config is what
        # actually reaches Docker and the sidecar.
        config = _bound_sandbox(deps).config

        assert config.extra_hosts == _HOST_GATEWAY_ALIAS
        assert config.allowed_hosts == ("host.docker.internal:3001",)
        # Both endpoints share one host:port, so the host allowlist alone would
        # also grant every other route the backend serves on it.
        assert config.allowed_paths == (
            "host.docker.internal:3001=/api/v1/gateway/v1",
            "host.docker.internal:3001=/api/v1/mcp-gateway",
        )
