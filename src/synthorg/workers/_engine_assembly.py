# module-kind: code
"""Boot ``AgentEngine`` assembly for the runtime-services builder.

Owns the engine-side construction steps behind
:func:`synthorg.workers.runtime_builder.build_runtime_services`: the
sandbox + tool registry, the optional external-access runtime, the
stakes router, the vision verifier gate, and the engine constructor
that threads every boot collaborator in.
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg._core.features import require_service
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.state import BudgetStateSlice
from synthorg.communication.state import CommunicationStateSlice
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.flight_recording import FlightRecorderSink
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.routing_policy import build_stakes_router
from synthorg.engine.state import task_engine_of
from synthorg.integrations.state import (
    IntegrationsStateSlice,
    connection_catalog_of,
)
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import code_execution_records_of
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of
from synthorg.tools.base import BaseTool
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.factory import build_sandbox_backends
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.workers._agent_engine_collaborators import (
    boot_brain_tool_factory_provider,
    boot_steering_inbox,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.routing_policy.router import StakesRouter
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.visionverify.protocol import VisionVerifierGate
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"
_EXTERNAL_API_NS: str = SettingNamespace.EXTERNAL_API.value


async def _build_tool_registry(
    app_state: AppState,
    workspace_root: Path,
    extra_tools: tuple[BaseTool, ...] = (),
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
    web_request_timeout = await config_resolver_of(app_state).get_float(
        _WEB_TIMEOUT_NS,
        _WEB_TIMEOUT_KEY,
    )
    from synthorg.tools.browser._settings import (  # noqa: PLC0415
        resolve_browser_settings,
    )
    from synthorg.tools.desktop._settings import (  # noqa: PLC0415
        resolve_desktop_settings,
    )

    browser_settings = await resolve_browser_settings(config_resolver_of(app_state))
    desktop_settings = await resolve_desktop_settings(config_resolver_of(app_state))
    lifecycle_strategy = create_lifecycle_strategy(
        app_state.config.sandboxing.docker.lifecycle,
        clock=app_state.clock,
    )
    sandbox_backends = build_sandbox_backends(
        config=app_state.config.sandboxing,
        workspace=workspace_root,
        lifecycle_strategy=lifecycle_strategy,
    )
    default_tools = build_default_tools_from_config(
        workspace=workspace_root,
        config=app_state.config,
        sandbox_backends=sandbox_backends,
        web_request_timeout=web_request_timeout,
        browser_settings=browser_settings,
        desktop_settings=desktop_settings,
        code_execution_records=code_execution_records_of(app_state),
    )
    tools: list[BaseTool] = [*default_tools, *extra_tools]
    return ToolRegistry(tools), len(tools), sandbox_backends


async def _build_external_api_runtime(
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


def _build_stakes_router_or_none(
    app_state: AppState,
    *,
    active_provider_name: str,
) -> StakesRouter | None:
    """Build the stakes-aware model router from live application state.

    Returns ``None`` when the benchmark provider is absent (cost-dial
    not wired, e.g. a persistence-less boot), so the engine simply skips
    stakes routing. Reads the benchmark provider and coordination-metrics
    store off ``AppState`` and builds a tier resolver scoped to the
    single active provider that the runtime executes against, so the
    router can never resolve a tier to a model owned by an inactive
    provider and hand it to the wrong client; ships the ``stakes_aware``
    default strategy.

    Returns:
        The ``StakesRouter``, or ``None`` when the benchmark provider or
        the active provider config is absent.
    """
    from synthorg.providers.routing.resolver import ModelResolver  # noqa: PLC0415

    benchmark_provider = app_state.slice(BudgetStateSlice).benchmark_provider
    if benchmark_provider is None:
        return None
    provider_cfg = app_state.config.providers.get(active_provider_name)
    if provider_cfg is None:
        return None
    resolver = ModelResolver.from_config({active_provider_name: provider_cfg})
    coordination_store = app_state.slice(CoordinationStateSlice).metrics_store
    return build_stakes_router(
        app_state.config.stakes_routing,
        benchmark_provider=benchmark_provider,
        resolver=resolver,
        coordination_store=coordination_store,
    )


def _construct_agent_engine(  # noqa: PLR0913 -- boot collaborators threaded in
    app_state: AppState,
    provider: CompletionProvider,
    registry: ProviderRegistry,
    tool_registry: ToolRegistry,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
    external_api_runtime: ExternalApiRuntime | None = None,
    *,
    active_provider_name: str,
    flight_recorder_sink: FlightRecorderSink | None = None,
) -> AgentEngine:
    """Assemble the boot ``AgentEngine`` from live application state.

    A single boot instance is shared by the worker execution service and the
    coordinator's parallel executor, so both observe the same interrupt store,
    event stream hub, clock seam, and shared ``coordination_metrics_collector``
    (the source of the single-agent baselines the multi-agent metrics compare).

    Returns:
        The boot ``AgentEngine`` shared by the worker execution service
        and the coordinator.
    """
    return AgentEngine(
        coordination_metrics_collector=coordination_metrics_collector,
        provider=provider,
        provider_registry=registry,
        tool_registry=tool_registry,
        stakes_router=_build_stakes_router_or_none(
            app_state, active_provider_name=active_provider_name
        ),
        cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        task_engine=task_engine_of(app_state),
        approval_store=require_service(
            app_state.slice(ApprovalStateSlice).store, "Approval Store"
        ),
        cost_forecast_repo=app_state.slice(BudgetStateSlice).cost_forecast_repo,
        approval_gate=app_state.slice(ApprovalStateSlice).gate,
        trust_service=app_state.slice(SecurityStateSlice).trust_service,
        mcp_self_consumer=build_mcp_self_consumer(
            app_state.config.security.mcp_self_consumer,
            app_state,
        ),
        security_config=app_state.config.security,
        audit_log=app_state.slice(SecurityStateSlice).audit_log,
        memory_backend=app_state.slice(MemoryStateSlice).backend,
        config_resolver=config_resolver_of(app_state),
        event_stream_hub=app_state.slice(CommunicationStateSlice).event_stream_hub,
        interrupt_store=app_state.slice(CommunicationStateSlice).interrupt_store,
        external_api_runtime=external_api_runtime,
        brain_tool_factory_provider=boot_brain_tool_factory_provider(app_state),
        flight_recorder_sink=flight_recorder_sink,
        steering_inbox=boot_steering_inbox(app_state),
        clock=app_state.clock,
    )


def _build_vision_gate_or_none(
    *,
    app_state: AppState,
    workspace_root: Path,
    provider: CompletionProvider | None,
) -> VisionVerifierGate | None:
    """Construct the vision verifier gate when the subsystem is enabled.

    Pulls :class:`VisionVerifyConfig` from
    ``app_state.config.security.vision_verify``. The ``heuristic`` /
    ``noop`` verifiers need only the workspace; the ``llm_vision``
    verifier additionally needs the active provider, pinned to the
    vendor-agnostic ``example-medium-001`` model id (operators override
    via the post-init swap path). A misconfigured ``llm_vision`` with no
    provider (empty company) degrades the gate to ``None`` with a
    warning rather than crashing boot.

    Returns:
        The ``VisionVerifierGate`` when the subsystem is enabled and
        buildable, otherwise ``None``.
    """
    from synthorg.security.visionverify.builder import (  # noqa: PLC0415
        build_vision_verifier_gate,
    )

    tier_resolver = (
        (lambda _tier: "example-medium-001") if provider is not None else None
    )
    try:
        return build_vision_verifier_gate(
            app_state.config.security.vision_verify,
            workspace=workspace_root,
            provider=provider,
            tier_resolver=tier_resolver,
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="runtime_services",
            note="vision verifier gate disabled: build failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
