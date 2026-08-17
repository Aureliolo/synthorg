# module-kind: orchestrator
"""Boot wiring for the external-MCP bridge tools and their sandbox policy.

Split from ``_engine_assembly`` so the tool-registry orchestrator stays within
its size budget: this module owns resolving the MCP sandbox policy (fail-secure
to sandbox-on defaults) and connecting the configured/installed MCP servers via
:class:`MCPToolFactory`, returning their discovered tools for the boot registry.
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.integrations.state import IntegrationsStateSlice, connection_catalog_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.state import config_resolver_of
from synthorg.tools.base import BaseTool
from synthorg.tools.mcp.sandbox import MCPSandboxConfig, SandboxNetwork
from synthorg.tools.sandbox.deployment_identity import deployment_id_for

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOLS_NS: str = "tools"


async def _resolve_mcp_sandbox_config(app_state: AppState) -> MCPSandboxConfig:
    """Resolve the MCP sandbox policy from settings, fail-secure to defaults.

    A resolve failure keeps sandboxing ON with default limits rather than
    silently spawning MCP servers on the host: the secure default wins when
    settings are unavailable.

    Returns:
        The resolved :class:`MCPSandboxConfig` (defaults on any resolve error).
    """
    resolver = config_resolver_of(app_state)
    # Derived from the same workspace root the agent sandboxes use, because the
    # reconciliation pass asks one question of every container it finds: which
    # deployment created it. An MCP runtime with no answer is never reclaimed.
    deployment_id = NotBlankStr(deployment_id_for(agent_workspace_root_of(app_state)))
    try:
        return MCPSandboxConfig(
            deployment_id=deployment_id,
            enabled=await resolver.get_bool(_TOOLS_NS, "mcp_sandbox_enabled"),
            # No ``image=``: the field resolves the one sandbox image the
            # deployment verified, rather than a second configurable one.
            memory_limit=await resolver.get_str(_TOOLS_NS, "mcp_sandbox_memory_limit"),
            pids_limit=await resolver.get_int(_TOOLS_NS, "mcp_sandbox_pids_limit"),
            cpus=await resolver.get_str(_TOOLS_NS, "mcp_sandbox_cpus"),
            # Validated against ``^(bridge|none|host)$`` at the settings layer;
            # the Literal field re-validates, so an out-of-set value falls
            # through to the fail-secure default below rather than slipping in.
            network=cast(
                "SandboxNetwork",
                await resolver.get_str(_TOOLS_NS, "mcp_sandbox_network"),
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- fail-secure to sandbox-on defaults
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="mcp_bridge",
            note="could not resolve MCP sandbox settings; using secure defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return MCPSandboxConfig(deployment_id=deployment_id)


async def build_mcp_bridge_tools(app_state: AppState) -> tuple[BaseTool, ...]:
    """Bridge configured external MCP servers into the boot tool registry.

    Merges catalog installations onto the YAML ``mcp`` config, connects to
    every enabled server via :class:`MCPToolFactory`, and returns the
    discovered tools so ``_build_tool_registry`` can expose them to agents.
    No servers configured -> no-op (empty tuple). The factory is parked on
    the integrations slice so the shutdown runner can close its sessions.
    Best-effort: an unreachable server degrades to no bridge tools rather
    than poisoning boot.

    Returns:
        The bridged external-MCP tools, or ``()`` when none are configured.
    """
    from synthorg.integrations.mcp_catalog.install import (  # noqa: PLC0415
        merge_installed_servers,
    )
    from synthorg.tools.mcp.factory import MCPToolFactory  # noqa: PLC0415

    base = app_state.config.mcp
    integrations = app_state.slice(IntegrationsStateSlice)
    existing_factory = integrations.mcp_bridge_factory
    if existing_factory is not None:
        # build_runtime_services re-runs this on post_setup_reinit; close the
        # prior factory's sessions before reconnecting so they do not leak,
        # and clear the slice so a no-servers reinit leaves nothing wired.
        try:
            await existing_factory.shutdown()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- degrade-to-None wiring
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                service="mcp_bridge",
                note="failed to close prior MCP bridge factory before rebuild",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        app_state.wire(IntegrationsStateSlice, mcp_bridge_factory=None)
    repo = integrations.mcp_installations_repo
    catalog = integrations.mcp_catalog_service
    merged = base
    if repo is not None and catalog is not None:
        installations = await repo.list_items(limit=10_000)
        if installations:
            entries_by_id = {entry.id: entry for entry in await catalog.browse()}
            merged = merge_installed_servers(base, installations, entries_by_id)
    if not merged.servers:
        return ()
    sandbox = await _resolve_mcp_sandbox_config(app_state)
    factory: MCPToolFactory | None = None
    try:
        factory = MCPToolFactory(
            merged,
            credential_source=connection_catalog_of(app_state),
            sandbox=sandbox,
        )
        tools = await factory.create_tools()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        # A factory that opened sessions before failing must release them;
        # shutdown() is self-guarding (per-client try/except + clear), so
        # only a critical propagates here, which is the correct behaviour.
        if factory is not None:
            await factory.shutdown()
        logger.warning(
            API_APP_STARTUP,
            service="mcp_bridge",
            note="external MCP bridge wiring failed; no bridge tools exposed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    app_state.wire(IntegrationsStateSlice, mcp_bridge_factory=factory)
    logger.info(
        API_APP_STARTUP,
        service="mcp_bridge",
        note="wired",
        tool_count=len(tools),
    )
    return tools
