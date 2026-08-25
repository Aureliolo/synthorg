# module-kind: orchestrator
"""Boot wiring for the governed forge / chat / deploy / publish agent-tool runtimes.

Ghost-wired builders behind
:func:`synthorg.workers.runtime_builder.build_runtime_services`: each
returns a boot-scoped runtime bundle only when its feature flag is on AND
its bound surface is non-empty, else ``None`` (so the tools are not
registered). Fail-open at distinct log levels so operators can tell a
transient flag-resolution miss (WARNING) from a misconfigured-once-enabled
feature (ERROR). Kept out of ``_engine_assembly`` so that orchestrator module
stays within its size budget.

Forge and chat each bind ONE connection; deploy and publish bind an operator
allowlist of targets and choose per call, so their empty-means-off check is an
empty allowlist rather than an unset connection name. The distinction is the
families' own (`allowed_targets` versus `connection_name`), not a wiring
choice: an agent picks from the operator's list and can never extend it.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.tools.chat._runtime import ChatToolsRuntime
    from synthorg.tools.deploy._runtime import DeployToolsRuntime
    from synthorg.tools.forge._runtime import ForgeToolsRuntime
    from synthorg.tools.publish._runtime import PublishToolsRuntime

logger = get_logger(__name__)

_TOOLS_NS: str = SettingNamespace.TOOLS.value


async def _resolve_tools_flag_or_none(
    app_state: AppState, key: str, *, service: str
) -> bool | None:
    """Resolve a boot-scoped ``tools.*`` boolean flag, fail-open to ``None``.

    Returns ``None`` (feature left off) when no connection catalog is wired
    or the flag cannot be resolved (transient); ``False`` when explicitly
    off; ``True`` when on.

    Returns:
        ``True`` / ``False`` for the resolved flag, or ``None`` when the
        feature must stay off (no catalog / resolution failure).
    """
    if app_state.slice(IntegrationsStateSlice).connection_catalog is None:
        return None
    try:
        return await config_resolver_of(app_state).get_bool(_TOOLS_NS, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service=service,
            context="enabled_flag_resolve",
            note=f"could not resolve {_TOOLS_NS}.{key}; feature left off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def build_forge_tools_runtime_or_none(
    app_state: AppState,
) -> ForgeToolsRuntime | None:
    """Resolve the boot-scoped forge-tools runtime, or ``None`` when off.

    Ghost-wired: built only when ``tools.forge_tools_enabled`` is on AND a
    non-empty ``tools.forge_tools_connection`` is bound.

    Returns:
        The configured ``ForgeToolsRuntime``, or ``None`` when the feature
        is off, no catalog / connection is wired, or resolution fails.
    """
    enabled = await _resolve_tools_flag_or_none(
        app_state, "forge_tools_enabled", service="forge_tools"
    )
    if not enabled:
        return None

    from synthorg.tools.forge._runtime import ForgeToolsRuntime  # noqa: PLC0415

    resolver = config_resolver_of(app_state)
    try:
        connection_name = await resolver.get_str(_TOOLS_NS, "forge_tools_connection")
        timeout_seconds = await resolver.get_float(
            _TOOLS_NS, "forge_tools_timeout_seconds"
        )
        max_read_chars = await resolver.get_int(_TOOLS_NS, "forge_tools_max_read_chars")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="forge_tools",
            context="forge_tools_runtime_resolve",
            note="forge tools misconfigured; not registered",
        )
        return None
    if not connection_name:
        return None
    connection_catalog = app_state.slice(IntegrationsStateSlice).connection_catalog
    if connection_catalog is None:
        return None
    return ForgeToolsRuntime(
        connection_catalog=connection_catalog,
        connection_name=connection_name,
        timeout_seconds=timeout_seconds,
        max_read_chars=max_read_chars,
    )


async def build_chat_tools_runtime_or_none(
    app_state: AppState,
) -> ChatToolsRuntime | None:
    """Resolve the boot-scoped chat-tools runtime, or ``None`` when off.

    Ghost-wired: built only when ``tools.chat_tools_enabled`` is on AND a
    non-empty ``tools.chat_tools_connection`` is bound.

    Returns:
        The configured ``ChatToolsRuntime``, or ``None`` when the feature
        is off, no catalog / connection is wired, or resolution fails.
    """
    enabled = await _resolve_tools_flag_or_none(
        app_state, "chat_tools_enabled", service="chat_tools"
    )
    if not enabled:
        return None

    from synthorg.tools.chat._runtime import ChatToolsRuntime  # noqa: PLC0415

    resolver = config_resolver_of(app_state)
    try:
        connection_name = await resolver.get_str(_TOOLS_NS, "chat_tools_connection")
        timeout_seconds = await resolver.get_float(
            _TOOLS_NS, "chat_tools_timeout_seconds"
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="chat_tools",
            context="chat_tools_runtime_resolve",
            note="chat tools misconfigured; not registered",
        )
        return None
    if not connection_name:
        return None
    connection_catalog = app_state.slice(IntegrationsStateSlice).connection_catalog
    if connection_catalog is None:
        return None
    return ChatToolsRuntime(
        connection_catalog=connection_catalog,
        connection_name=connection_name,
        timeout_seconds=timeout_seconds,
    )


def _parse_targets(raw: str) -> frozenset[str]:
    """Parse a comma-separated target allowlist.

    Returns:
        The set of non-blank target names. Empty allows nothing, which is
        what makes an unset allowlist leave the family unregistered rather
        than registered against every connection in the catalog.
    """
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


async def build_deploy_tools_runtime_or_none(
    app_state: AppState,
) -> DeployToolsRuntime | None:
    """Resolve the boot-scoped deploy-tools runtime, or ``None`` when off.

    Ghost-wired: built only when ``tools.deploy_tools_enabled`` is on AND
    ``tools.deploy_tools_targets`` names at least one target.

    Returns:
        The configured ``DeployToolsRuntime``, or ``None`` when the feature
        is off, no catalog / target is configured, or resolution fails.
    """
    enabled = await _resolve_tools_flag_or_none(
        app_state, "deploy_tools_enabled", service="deploy_tools"
    )
    if not enabled:
        return None

    from synthorg.tools.deploy._runtime import DeployToolsRuntime  # noqa: PLC0415

    resolver = config_resolver_of(app_state)
    try:
        targets = _parse_targets(
            await resolver.get_str(_TOOLS_NS, "deploy_tools_targets")
        )
        timeout_seconds = await resolver.get_float(
            _TOOLS_NS, "deploy_tools_timeout_seconds"
        )
        max_log_chars = await resolver.get_int(_TOOLS_NS, "deploy_tools_max_log_chars")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="deploy_tools",
            context="deploy_tools_runtime_resolve",
            note="deploy tools misconfigured; not registered",
        )
        return None
    if not targets:
        return None
    connection_catalog = app_state.slice(IntegrationsStateSlice).connection_catalog
    if connection_catalog is None:
        return None
    return DeployToolsRuntime(
        connection_catalog=connection_catalog,
        allowed_targets=targets,
        timeout_seconds=timeout_seconds,
        max_log_chars=max_log_chars,
    )


async def build_publish_tools_runtime_or_none(
    app_state: AppState,
) -> PublishToolsRuntime | None:
    """Resolve the boot-scoped publish-tools runtime, or ``None`` when off.

    Ghost-wired: built only when ``tools.publish_tools_enabled`` is on AND
    ``tools.publish_tools_targets`` names at least one registry.

    The workspace root comes from the agent workspace slice rather than a
    setting of its own: ``publish_push`` reads a built OCI layout from under
    the same tree the agent's shell tool built it in, so a second answer to
    where that tree is would let a push read from somewhere nothing wrote.

    Returns:
        The configured ``PublishToolsRuntime``, or ``None`` when the feature
        is off, no catalog / target is configured, or resolution fails.
    """
    enabled = await _resolve_tools_flag_or_none(
        app_state, "publish_tools_enabled", service="publish_tools"
    )
    if not enabled:
        return None

    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.tools.publish._runtime import PublishToolsRuntime  # noqa: PLC0415

    resolver = config_resolver_of(app_state)
    try:
        targets = _parse_targets(
            await resolver.get_str(_TOOLS_NS, "publish_tools_targets")
        )
        timeout_seconds = await resolver.get_float(
            _TOOLS_NS, "publish_tools_timeout_seconds"
        )
        max_manifest_bytes = await resolver.get_int(
            _TOOLS_NS, "publish_tools_max_manifest_bytes"
        )
        max_image_bytes = await resolver.get_int(
            _TOOLS_NS, "publish_tools_max_image_bytes"
        )
        workspace_root = agent_workspace_root_of(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="publish_tools",
            context="publish_tools_runtime_resolve",
            note="publish tools misconfigured; not registered",
        )
        return None
    if not targets:
        return None
    connection_catalog = app_state.slice(IntegrationsStateSlice).connection_catalog
    if connection_catalog is None:
        return None
    return PublishToolsRuntime(
        connection_catalog=connection_catalog,
        allowed_targets=targets,
        timeout_seconds=timeout_seconds,
        max_manifest_bytes=max_manifest_bytes,
        max_image_bytes=max_image_bytes,
        workspace_root=workspace_root,
    )


async def build_connection_tool_runtimes(
    app_state: AppState,
) -> ConnectionToolRuntimes:
    """Resolve every governed connection-tool family for one runtime build.

    The single owner of "which families a runtime carries": a family added
    to the bundle without a resolve here is a family permanently off, and a
    resolve without a bundle field is one the engine never sees.

    Returns:
        The bundle, with a ``None`` for each family that is off or unbound.
    """
    return ConnectionToolRuntimes(
        forge=await build_forge_tools_runtime_or_none(app_state),
        chat=await build_chat_tools_runtime_or_none(app_state),
        deploy=await build_deploy_tools_runtime_or_none(app_state),
        publish=await build_publish_tools_runtime_or_none(app_state),
    )


__all__ = [
    "build_chat_tools_runtime_or_none",
    "build_connection_tool_runtimes",
    "build_deploy_tools_runtime_or_none",
    "build_forge_tools_runtime_or_none",
    "build_publish_tools_runtime_or_none",
]
