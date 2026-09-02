# module-kind: orchestrator
"""The tool registry an agent starts from, and the external-access runtime.

Split from :mod:`synthorg.workers.engine_assembly` on the same rule its
other siblings were: that module is the ONE place an ``AgentEngine`` is
constructed, and reading it should be reading the wiring rather than
scrolling past the sandbox and the HTTP client on the way to it.

Both functions here answer to a workspace root the caller owns, which is
the same reason they are not inside the assembly: the engine holds no
workspace root.
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import IntegrationsStateSlice, connection_catalog_of
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.memory_protocol import OrgFactRepository
from synthorg.persistence.state import (
    PersistenceStateSlice,
    code_execution_records_of,
)
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of
from synthorg.tools.base import BaseTool
from synthorg.tools.ceilings import ToolCeilings
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.factory import (
    build_sandbox_backends,
    merge_secure_backend_defaults,
)
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.tools.web.fetch_types import WebFetchRungs, WebToolsWiring
from synthorg.tools.web.providers.http_search_provider import HttpWebSearchProvider
from synthorg.workers._background_job_wiring import (
    background_job_registry_or_none,
    bind_pin_check_if_wired,
    resolve_background_job_ceilings,
)
from synthorg.workers._image_provider_wiring import build_image_provider_or_none
from synthorg.workers._memory_assembly import wiki_exporter_or_none

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"
_TOOLS_NS: str = "tools"
_GIT_LOG_MAX_COUNT_KEY: str = "git_log_max_count"
_CODE_RUNNER_OUTPUT_TAIL_KEY: str = "code_runner_output_tail_limit"
_EXTERNAL_API_NS: str = SettingNamespace.EXTERNAL_API.value


def _org_fact_store_or_none(app_state: AppState) -> OrgFactRepository | None:
    """Resolve the org-fact store, or ``None`` before persistence connects.

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    return None if persistence is None else persistence.org_facts


def _tracked_container_repo_or_none(
    app_state: AppState,
) -> TrackedContainerRepository | None:
    """Resolve the tracked-container store, or ``None`` before persistence connects.

    A Docker backend built without this repository tracks its containers
    in a dict that dies with the process, so the boot reconciliation pass
    reads an empty table and every live sandbox looks like an orphan. The
    repository is what makes "no row" mean orphan rather than "we never
    wrote one".

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None or not persistence.is_connected:
        return None
    return persistence.tracked_containers


async def build_tool_registry(
    app_state: AppState,
    workspace_root: Path,
    extra_tools: tuple[BaseTool, ...] = (),
    *,
    search_provider: HttpWebSearchProvider | None = None,
    fetch_rungs: WebFetchRungs | None = None,
) -> tuple[ToolRegistry, int, Mapping[str, SandboxBackend]]:
    """Create the sandbox workspace and the config-driven tool registry.

    Constructs the config-selected sandbox lifecycle strategy
    (per-agent / per-task / per-call) at the boot site with the
    application clock, builds the per-category sandbox backends with it
    injected, then wires the tool registry against those backends.  The
    backends mapping is returned so the execution service can release
    the lifecycle owner at the task boundary and shut backends down.

    The ``extra_tools`` parameter accepts BOOT-time tools that must
    join the registry before any agent runs (e.g. the red-team gate's
    ``submit_red_team_report`` tool). They are appended to the
    config-driven default tools so the resulting registry sees every
    tool the agent engine should expose.

    Returns:
        A ``(registry, tool_count, sandbox_backends)`` triple: the wired
        tool registry, the number of tools, and the per-category sandbox
        backends.
    """
    await asyncio.to_thread(
        workspace_root.mkdir,
        parents=True,
        exist_ok=True,
    )
    resolver = config_resolver_of(app_state)
    web_request_timeout = await resolver.get_float(_WEB_TIMEOUT_NS, _WEB_TIMEOUT_KEY)
    git_log_max_count = await resolver.get_int(_TOOLS_NS, _GIT_LOG_MAX_COUNT_KEY)
    code_runner_output_tail_limit = await resolver.get_int(
        _TOOLS_NS, _CODE_RUNNER_OUTPUT_TAIL_KEY
    )
    (
        background_max_concurrent_jobs,
        background_output_byte_cap,
    ) = await resolve_background_job_ceilings(resolver)
    ceilings = ToolCeilings(
        git_log_max_count=git_log_max_count,
        code_runner_output_tail_limit=code_runner_output_tail_limit,
        background_max_concurrent_jobs=background_max_concurrent_jobs,
        background_output_byte_cap=background_output_byte_cap,
    )
    from synthorg.tools.browser._settings import (  # noqa: PLC0415
        resolve_browser_settings,
    )
    from synthorg.tools.desktop._settings import (  # noqa: PLC0415
        resolve_desktop_settings,
    )

    browser_settings = await resolve_browser_settings(resolver)
    desktop_settings = await resolve_desktop_settings(resolver)
    background_jobs = background_job_registry_or_none(app_state)
    lifecycle_strategy = create_lifecycle_strategy(
        app_state.config.sandboxing.docker.lifecycle,
        clock=app_state.clock,
    )
    # Force untrusted-exec categories onto the container backend so the
    # built map contains the docker backend the tool factory resolves to
    # (the factory applies the same merge to its per-category lookup).
    sandbox_backends = build_sandbox_backends(
        config=merge_secure_backend_defaults(app_state.config.sandboxing),
        workspace=workspace_root,
        tracked_container_repo=_tracked_container_repo_or_none(app_state),
        lifecycle_strategy=lifecycle_strategy,
        background_jobs=background_jobs,
        ceilings=ceilings,
    )
    bind_pin_check_if_wired(
        lifecycle_strategy=lifecycle_strategy,
        sandbox_backends=sandbox_backends,
        background_jobs=background_jobs,
    )
    image_provider = await build_image_provider_or_none(app_state)
    default_tools = build_default_tools_from_config(
        workspace=workspace_root,
        config=app_state.config,
        sandbox_backends=sandbox_backends,
        ceilings=ceilings,
        # Handed the resolver rather than a resolved number: the command
        # ceiling is read per command, so an operator raising it applies to
        # the next command an agent runs rather than to the next rebuild.
        config_resolver=resolver,
        browser_settings=browser_settings,
        desktop_settings=desktop_settings,
        code_execution_records=code_execution_records_of(app_state),
        image_provider=image_provider,
        web=WebToolsWiring(
            request_timeout=web_request_timeout,
            search_provider=search_provider,
            fetch_rungs=fetch_rungs,
        ),
        # Without these three the Knowledge-Architect tool set builds
        # empty, which is how org memory stayed unreachable from an agent
        # even though its backend was wired at boot.
        org_memory_backend=app_state.slice(MemoryStateSlice).org_memory_backend,
        org_fact_store=_org_fact_store_or_none(app_state),
        wiki_exporter=wiki_exporter_or_none(app_state),
    )
    tools: list[BaseTool] = [*default_tools, *extra_tools]
    return ToolRegistry(tools), len(tools), sandbox_backends


async def build_external_api_runtime(
    app_state: AppState,
) -> ExternalApiRuntime | None:
    """Resolve the boot-scoped external-access runtime, or ``None`` when off.

    Returns ``None`` (so the tool is not registered) when the feature flag
    is disabled or no connection catalog is wired. Otherwise resolves the
    provider discriminator and default per-call limits via the settings
    resolver and builds the configured ``ExternalAccessProvider``.

    Fail-open in both failure modes (a misconfigured external-access feature
    must not crash the whole agent runtime), but at distinct log levels so
    operators can tell them apart:

    - A failure resolving the ``enabled`` flag is treated as transient and
      logged at WARNING; the feature is simply left off this boot.
    - A failure building the runtime once enabled (unknown provider
      discriminator, missing/invalid limit setting) is an operator
      misconfiguration and logged at ERROR, so a silently-disabled feature
      is never mistaken for an intentional one.

    Returns:
        The configured ``ExternalApiRuntime``, or ``None`` when the
        feature is off, no catalog is wired, or resolution / build fails.
    """
    if app_state.slice(IntegrationsStateSlice).connection_catalog is None:
        return None
    resolver = config_resolver_of(app_state)
    try:
        enabled = await resolver.get_bool(_EXTERNAL_API_NS, "enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="external_api",
            context="enabled_flag_resolve",
            note="could not resolve external_api.enabled; feature left off",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not enabled:
        return None

    from synthorg.tools.external_api._runtime import ExternalApiRuntime  # noqa: PLC0415
    from synthorg.tools.external_api.provider_factory import (  # noqa: PLC0415
        build_external_access_provider,
    )

    try:
        provider_type = await resolver.get_str(_EXTERNAL_API_NS, "provider_type")
        max_response_bytes = await resolver.get_int(
            _EXTERNAL_API_NS,
            "default_max_response_bytes",
        )
        timeout_seconds = await resolver.get_float(
            _EXTERNAL_API_NS,
            "default_timeout_seconds",
        )
        default_max_rpm = await resolver.get_int(_EXTERNAL_API_NS, "default_max_rpm")
        provider = build_external_access_provider(provider_type=provider_type)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="external_api",
            context="external_api_runtime_resolve",
            note="external-access misconfigured; tool not registered",
        )
        return None

    web = app_state.config.web
    network_policy = (
        web.network_policy
        if web is not None and web.network_policy is not None
        else None
    )
    return ExternalApiRuntime(
        connection_catalog=connection_catalog_of(app_state),
        provider=provider,
        network_policy=network_policy or NetworkPolicy(),
        max_response_bytes=max_response_bytes,
        timeout_seconds=timeout_seconds,
        default_max_rpm=default_max_rpm,
    )


__all__ = ["build_external_api_runtime", "build_tool_registry"]
