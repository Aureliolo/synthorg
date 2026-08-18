# module-kind: orchestrator
"""Boot wiring for the external-MCP bridge tools and their sandbox policy.

Split from ``_engine_assembly`` so the tool-registry orchestrator stays within
its size budget: this module owns resolving the MCP sandbox policy (fail-secure
to sandbox-on defaults) and connecting the configured/installed MCP servers via
:class:`MCPToolFactory`, returning their discovered tools for the boot registry.
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr, require_not_blank
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
    deployment_id: NotBlankStr | None = None
    runtime: NotBlankStr | None = None
    # Guarded separately from the resolve below, and not folded into it: both
    # derivations can raise on an app state that is not fully wired, and this
    # helper is called from OUTSIDE its caller's own handler, so an escape
    # here poisons boot rather than degrading to no bridge tools. The fallback
    # return reads these too, which is why a raise cannot be allowed to leave
    # them unbound.
    #
    # ONE BLOCK EACH, deliberately. The two are independent facts, and sharing
    # a guard orders them: the id is derived first, so anything that raises
    # there skips the runtime read entirely and leaves it ``None``. ``None``
    # is the daemon default, which silently downgrades the isolation on the
    # one path in this product that runs code nobody reviewed, in exchange for
    # an unrelated failure to name the deployment. A blank id is a container
    # nobody can reclaim; a lost runtime is a container nobody is contained
    # by, and neither may be paid for with the other.
    try:
        # Derived from the same workspace root the agent sandboxes use,
        # because the reconciliation pass asks one question of every
        # container it finds: which deployment created it. An MCP runtime
        # with no answer is never reclaimed.
        # Checked HERE, where the guard below can still absorb it. The
        # annotation alone does not check: it only runs inside a Pydantic
        # model, so a blank derivation travels as ``""`` and first fails
        # validation in the fallback construction, which sits inside the
        # handler whose whole job is to guarantee a return. A raise there has
        # nothing left to catch it and takes boot down, which is the outcome
        # this helper exists to prevent. Failing here instead leaves the id
        # unset and the container unattributed, which the handler reports.
        deployment_id = NotBlankStr(
            require_not_blank(
                deployment_id_for(agent_workspace_root_of(app_state)),
                "deployment_id",
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- an unattributed container is recoverable;
        # a boot that cannot wire MCP at all is not
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="mcp_bridge",
            note="could not derive MCP sandbox identity; container is unattributed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    try:
        # The same runtime the agent sandbox uses. An operator who installed
        # gVisor did it to contain code they do not trust, and this is the
        # path that runs code nobody reviewed at all: taking the daemon
        # default here while honouring their choice for their own agents
        # would give the weaker isolation to the stronger threat, silently,
        # because the two configs read as siblings.
        runtime = app_state.config.sandboxing.docker.runtime
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the daemon default still isolates; a boot
        # that cannot wire MCP at all does not
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="mcp_bridge",
            note=(
                "could not read the configured sandbox runtime; MCP containers "
                "fall back to the daemon default and lose any configured gVisor"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    try:
        # Resolved inside the guard, not above it: an unwired resolver raises
        # ``ServiceUnavailableError``, which is the ordinary state before
        # persistence connects and must reach the secure default rather than
        # the caller.
        resolver = config_resolver_of(app_state)
        return MCPSandboxConfig(
            deployment_id=deployment_id,
            runtime=runtime,
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
        return MCPSandboxConfig(deployment_id=deployment_id, runtime=runtime)


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
