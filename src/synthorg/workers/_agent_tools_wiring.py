# module-kind: orchestrator
"""Boot wiring for the governed forge / chat agent-tool runtimes.

Ghost-wired builders behind
:func:`synthorg.workers.runtime_builder.build_runtime_services`: each
returns a boot-scoped runtime bundle only when its feature flag is on AND
a connection is bound, else ``None`` (so the tools are not registered).
Fail-open at distinct log levels so operators can tell a transient
flag-resolution miss (WARNING) from a misconfigured-once-enabled feature
(ERROR). Kept out of ``_engine_assembly`` so that orchestrator module
stays within its size budget.
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

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.tools.chat._runtime import ChatToolsRuntime
    from synthorg.tools.forge._runtime import ForgeToolsRuntime

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


__all__ = [
    "build_chat_tools_runtime_or_none",
    "build_forge_tools_runtime_or_none",
]
