# module-kind: orchestrator
"""Boot wiring for the OpenHands execution loop.

Builds the loop's config and injected dependencies from live application
state. The dependencies are ``None`` (loop unavailable, fails loud only if
selected) unless the gateway signer and the sandbox-reachable gateway /
credentialed-MCP endpoints are all configured.
"""

from typing import TYPE_CHECKING

from synthorg.engine.openhands.config import (
    OpenHandsLoopConfig,
    OpenHandsLoopDeps,
)
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState


async def build_openhands_loop_config(app_state: AppState) -> OpenHandsLoopConfig:
    """Build the OpenHands loop config from live settings.

    Returns:
        The frozen loop config; the per-run bearer TTL tracks
        ``providers.gateway_token_ttl_seconds`` so it matches the gateway.
    """
    resolver = config_resolver_of(app_state)
    ttl = await resolver.get_int("providers", "gateway_token_ttl_seconds")
    return OpenHandsLoopConfig(token_ttl_seconds=ttl)


async def build_openhands_loop_deps_or_none(
    app_state: AppState,
) -> OpenHandsLoopDeps | None:
    """Wire the OpenHands loop dependencies when the gateway is available.

    The loop mints per-run gateway bearers with the *same* signer the
    gateway verifies with, so the signer is pulled from the gateway feature
    slice rather than built anew. Returns ``None`` when the signer or the
    sandbox-reachable endpoints are unset, leaving the loop unavailable (it
    fails loud only if an operator selects it).

    Returns:
        The wired dependencies, or ``None`` when the boundary is unwired.
    """
    from synthorg.api.gateway.state import GatewayStateSlice  # noqa: PLC0415
    from synthorg.engine.openhands.sdk_runtime import (  # noqa: PLC0415
        build_sdk_conversation,
    )

    signer = app_state.slice(GatewayStateSlice).signer
    if signer is None:
        return None
    resolver = config_resolver_of(app_state)
    gateway_base_url = await resolver.get_str("providers", "gateway_base_url")
    mcp_base_url = await resolver.get_str("tools", "credentialed_mcp_base_url")
    if not gateway_base_url or not mcp_base_url:
        return None
    return OpenHandsLoopDeps(
        build_conversation=build_sdk_conversation,
        signer=signer,
        gateway_base_url=gateway_base_url,
        mcp_base_url=mcp_base_url,
        clock=app_state.clock,
    )
