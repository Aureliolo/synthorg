# module-kind: orchestrator
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
from synthorg.engine.artifacts.expected_artifact_check import workspace_artifact_probe
from synthorg.engine.flight_recording import FlightRecorderSink
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.recovery import RecoveryStrategy
from synthorg.engine.recovery_factory import build_recovery_strategy
from synthorg.engine.routing_policy import build_stakes_router
from synthorg.engine.stagnation import create_stagnation_detector
from synthorg.engine.state import task_engine_of
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.hr.state import agent_registry_of
from synthorg.integrations.state import IntegrationsStateSlice, connection_catalog_of
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.evolution import EVOLUTION_PROPOSER_MODEL_UNSET
from synthorg.persistence.memory_protocol import OrgFactRepository
from synthorg.persistence.state import (
    PersistenceStateSlice,
    code_execution_records_of,
    project_repository_of,
)
from synthorg.providers.model_binding import resolve_bound_completion
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of
from synthorg.tools.base import BaseTool
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.factory import (
    build_sandbox_backends,
    merge_secure_backend_defaults,
)
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.tools.web.providers.http_search_provider import HttpWebSearchProvider
from synthorg.workers._agent_engine_collaborators import (
    boot_brain_tool_factory_provider,
    boot_docs_tool_factory_provider,
    boot_knowledge_tool_factory_provider,
    boot_research_tool_factory_provider,
    boot_steering_inbox,
    boot_structure_map_tool_factory_provider,
)
from synthorg.workers._agent_middleware_assembly import (
    build_agent_middleware_chain_or_none,
)
from synthorg.workers._classification_assembly import build_classification
from synthorg.workers._image_provider_wiring import build_image_provider_or_none
from synthorg.workers._memory_assembly import (
    build_memory_injection_strategy_or_none,
    resolved_procedural_config,
    wiki_exporter_or_none,
)
from synthorg.workers._openhands_wiring import (
    build_auto_loop_config_or_none,
    build_openhands_loop_config,
    build_openhands_loop_deps_or_none,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.evolution.service import EvolutionService
    from synthorg.engine.quality.classifier import StepQualityClassifier
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.engine.routing_policy.router import StakesRouter
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.tools.chat._runtime import ChatToolsRuntime
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.forge._runtime import ForgeToolsRuntime
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"
_TOOLS_NS: str = "tools"
_GIT_LOG_MAX_COUNT_KEY: str = "git_log_max_count"
_CODE_RUNNER_OUTPUT_TAIL_KEY: str = "code_runner_output_tail_limit"
_EXTERNAL_API_NS: str = SettingNamespace.EXTERNAL_API.value


async def _build_tool_registry(
    app_state: AppState,
    workspace_root: Path,
    extra_tools: tuple[BaseTool, ...] = (),
    *,
    search_provider: HttpWebSearchProvider | None = None,
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
    # Force untrusted-exec categories onto the container backend so the
    # built map contains the docker backend the tool factory resolves to
    # (the factory applies the same merge to its per-category lookup).
    sandbox_backends = build_sandbox_backends(
        config=merge_secure_backend_defaults(app_state.config.sandboxing),
        workspace=workspace_root,
        lifecycle_strategy=lifecycle_strategy,
    )
    image_provider = await build_image_provider_or_none(app_state)
    default_tools = build_default_tools_from_config(
        workspace=workspace_root,
        config=app_state.config,
        sandbox_backends=sandbox_backends,
        web_request_timeout=web_request_timeout,
        git_log_max_count=git_log_max_count,
        code_runner_output_tail_limit=code_runner_output_tail_limit,
        browser_settings=browser_settings,
        desktop_settings=desktop_settings,
        code_execution_records=code_execution_records_of(app_state),
        image_provider=image_provider,
        web_search_provider=search_provider,
        # Without these three the Knowledge-Architect tool set builds
        # empty, which is how org memory stayed unreachable from an agent
        # even though its backend was wired at boot.
        org_memory_backend=app_state.slice(MemoryStateSlice).org_memory_backend,
        org_fact_store=_org_fact_store_or_none(app_state),
        wiki_exporter=wiki_exporter_or_none(app_state),
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


async def _build_stakes_router_or_none(
    app_state: AppState,
) -> StakesRouter | None:
    """Build the stakes-aware model router from live application state.

    Builds a tier resolver over the LIVE provider set (the persisted configs
    the resolver serves, falling back to the boot ``RootConfig.providers``),
    not the boot-time config snapshot, so a DB-backed deployment routes over
    the providers actually in force. Each model's routing tier is the effective
    assignment from the :class:`TierAssignmentService` (deterministic heuristic
    classification overlaid by operator / LLM overrides), and its tool
    capability is read from capability metadata, so the router can gate on both.
    Uses a deterministic :class:`CheapestSelector` so a tier resolves to the
    cheapest model serving it across providers. The engine then swaps the
    dispatched client to the routed model's provider
    (``AgentEngine._resolve_provider_instance``), keeping the API called and the
    cost attribution on the same provider; ships the ``stakes_aware`` default
    strategy.

    Returns:
        The ``StakesRouter``, or ``None`` when no providers are configured.
    """
    from synthorg.providers.routing.resolver import ModelResolver  # noqa: PLC0415
    from synthorg.providers.routing.selector import CheapestSelector  # noqa: PLC0415
    from synthorg.workers._tier_assignment_wiring import (  # noqa: PLC0415
        build_tier_assignment_service,
    )

    # Prefer the live persisted provider set; fall back to the boot snapshot
    # when the resolver is not wired (anonymous / test boots) so routing still
    # builds. ``get_provider_configs`` itself falls back to ``RootConfig`` when
    # no DB override is set, so the two paths agree on a YAML-only deployment.
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if config_resolver is None:
        providers = dict(app_state.config.providers)
    else:
        providers = dict(await config_resolver.get_provider_configs())
    if not providers:
        return None
    tier_service = build_tier_assignment_service(app_state)
    tier_map = await tier_service.tier_lookup(providers)
    resolver = ModelResolver.from_config(
        providers,
        selector=CheapestSelector(),
        tier_map=tier_map,
    )
    coordination_store = app_state.slice(CoordinationStateSlice).metrics_store
    return build_stakes_router(
        app_state.config.stakes_routing,
        resolver=resolver,
        coordination_store=coordination_store,
    )


async def _build_auto_review_pipeline_or_none(
    app_state: AppState,
) -> ReviewPipeline | None:
    """Build the auto-review pipeline when the operator has opted in.

    Returns ``None`` unless ``engine.auto_review_on_completion`` is set, so
    the agent runtime is threaded a pipeline only when auto-review is enabled;
    absent it, ``apply_post_execution_transitions`` leaves completed work in
    IN_REVIEW for a human, exactly as before.

    Returns:
        A default (internal-only) :class:`ReviewPipeline`, or ``None``.
    """
    from synthorg.engine.review.factory import (  # noqa: PLC0415
        build_review_pipeline,
    )

    enabled = await config_resolver_of(app_state).get_bool(
        "engine", "auto_review_on_completion"
    )
    if not enabled:
        return None
    return build_review_pipeline()


async def _build_evolution_service_or_none(
    app_state: AppState,
) -> EvolutionService | None:
    """Build the agent self-evolution service when enabled at boot.

    Off by default (``evolution.enabled``). Gated on a connected persistence
    backend exposing the identity-version repo plus the agent registry and
    performance tracker; a missing dependency leaves the service ``None`` so
    the post-execution evolution trigger is a no-op. A factory failure (e.g.
    shadow-evaluation enabled without a runner) degrades to ``None`` rather
    than poisoning the boot engine.

    Returns:
        The wired evolution service, or ``None`` when disabled / unavailable.
    """
    config = app_state.config.evolution
    if not config.enabled:
        return None
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    persistence = app_state.slice(PersistenceStateSlice).backend
    if (
        persistence is None
        or not getattr(persistence, "is_connected", False)
        or not hasattr(persistence, "identity_versions")
    ):
        return None
    registry = app_state.slice(HrStateSlice).agent_registry
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if registry is None or tracker is None:
        return None
    from synthorg.engine.evolution.factory import (  # noqa: PLC0415
        build_evolution_service,
    )
    from synthorg.meta.state import evolution_outcome_store_of  # noqa: PLC0415
    from synthorg.versioning import VersioningService  # noqa: PLC0415

    try:
        service = build_evolution_service(
            config,
            registry=registry,
            versioning=VersioningService(persistence.identity_versions),
            tracker=tracker,
            memory_backend=app_state.slice(MemoryStateSlice).backend,
            # Evolution rewrites agent identities, so what analyses them is
            # the operator's explicit choice, never a borrowed connection.
            proposer_binding=await resolve_bound_completion(
                app_state,
                namespace="engine",
                key="evolution_proposer_model",
                unset_event=EVOLUTION_PROPOSER_MODEL_UNSET,
                subject="evolution proposer",
            ),
            outcome_sink=evolution_outcome_store_of(app_state),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrade-to-None wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="evolution",
            note="evolution service wiring failed; trigger stays a no-op",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    app_state.wire(EngineStateSlice, evolution_service=service)
    logger.info(API_APP_STARTUP, service="evolution", note="wired")
    return service


def _org_fact_store_or_none(app_state: AppState) -> OrgFactRepository | None:
    """Resolve the org-fact store, or ``None`` before persistence connects.

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    return None if persistence is None else persistence.org_facts


def _build_compaction_callback(
    app_state: AppState,
    provider: CompletionProvider,
) -> CompactionCallback:
    """Build the boot compaction callback from the live config.

    Text compaction is always wired (the callback fires once the context
    fill threshold is reached). The semantic LLM summariser and memory
    offloader are built only when their config flags are on and their
    collaborator (provider / memory backend) is present; otherwise the
    callback degrades to the text summary.

    Returns:
        The compaction callback for the boot ``AgentEngine``.
    """
    from synthorg.engine.compaction.llm_summarizer import LLMSummarizer  # noqa: PLC0415
    from synthorg.engine.compaction.memory_offload import (  # noqa: PLC0415
        MemoryOffloader,
    )
    from synthorg.engine.compaction.summarizer import (  # noqa: PLC0415
        make_compaction_callback,
    )

    config = app_state.config.compaction
    summarizer = None
    if config.llm_summarizer_enabled and config.llm_summary_model is not None:
        summarizer = LLMSummarizer(
            provider=provider,
            model=config.llm_summary_model,
            temperature=config.llm_summary_temperature,
            max_tokens=config.llm_summary_max_tokens,
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        )
    offloader = None
    backend = app_state.slice(MemoryStateSlice).backend
    if config.memory_offload_enabled and backend is not None:
        offloader = MemoryOffloader(backend=backend)
    return make_compaction_callback(
        config=config, summarizer=summarizer, offloader=offloader
    )


async def _construct_agent_engine(  # noqa: PLR0913 -- boot collaborators threaded in
    app_state: AppState,
    provider: CompletionProvider,
    *,
    registry: ProviderRegistry,
    tool_registry: ToolRegistry,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
    external_api_runtime: ExternalApiRuntime | None = None,
    forge_tools_runtime: ForgeToolsRuntime | None = None,
    chat_tools_runtime: ChatToolsRuntime | None = None,
    flight_recorder_sink: FlightRecorderSink | None = None,
    step_classifier: StepQualityClassifier | None = None,
    classification_detector_timeout_seconds: float | None = None,
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
    error_taxonomy_config, classification_sinks = build_classification(
        app_state,
        detector_timeout_seconds=classification_detector_timeout_seconds,
    )
    return AgentEngine(
        agent_middleware_chain=await build_agent_middleware_chain_or_none(
            app_state,
            error_taxonomy_config=error_taxonomy_config,
        ),
        coordination_metrics_collector=coordination_metrics_collector,
        error_taxonomy_config=error_taxonomy_config,
        classification_sinks=classification_sinks,
        evolution_service=await _build_evolution_service_or_none(app_state),
        policy_engine=app_state.slice(SecurityStateSlice).policy_engine,
        provider=provider,
        provider_registry=registry,
        tool_registry=tool_registry,
        stakes_router=await _build_stakes_router_or_none(app_state),
        agent_registry=agent_registry_of(app_state),
        cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        task_engine=task_engine_of(app_state),
        # Membership and per-project budget are validated against this repo
        # before a run starts; a work task refuses to run without it, so the
        # composition root hands it over rather than leaving the engine to
        # discover it is missing at dispatch.
        project_repo=project_repository_of(app_state),
        approval_store=require_service(
            app_state.slice(ApprovalStateSlice).store, "Approval Store"
        ),
        review_gate=app_state.slice(ApprovalStateSlice).review_gate,
        review_pipeline=await _build_auto_review_pipeline_or_none(app_state),
        # The engine holds no workspace root, so the layout knowledge stays
        # here and it receives the question it can ask: did this project
        # produce what its task declared. Bound to the same root the agent's
        # file tools write through, so the check reads what the run wrote.
        artifact_probe=workspace_artifact_probe(agent_workspace_root_of(app_state)),
        clarification_enabled=await config_resolver_of(app_state).get_bool(
            "engine", "clarification_enabled"
        ),
        scoping_enabled=await config_resolver_of(app_state).get_bool(
            "engine", "scoping_enabled"
        ),
        cost_forecast_repo=app_state.slice(BudgetStateSlice).cost_forecast_repo,
        approval_gate=app_state.slice(ApprovalStateSlice).gate,
        mcp_self_consumer=build_mcp_self_consumer(
            app_state.config.security.mcp_self_consumer,
            app_state,
        ),
        security_config=app_state.config.security,
        # Live security config the per-request interceptor reads, so an
        # operator toggle of the security.* flags applies without a restart.
        security_config_provider=lambda: app_state.security_runtime_config.current,
        audit_log=app_state.slice(SecurityStateSlice).audit_log,
        memory_backend=app_state.slice(MemoryStateSlice).backend,
        memory_injection_strategy=build_memory_injection_strategy_or_none(
            app_state,
            provider=provider,
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        ),
        # The write side: without these an agent recalls but never
        # learns, so a second run of the same objective starts from
        # nothing. The engine re-reads the capture switch per task, so
        # this is the boot fallback rather than the live value.
        procedural_memory_config=await resolved_procedural_config(app_state),
        distillation_capture_enabled=await config_resolver_of(app_state).get_bool(
            "memory", "distillation_capture_enabled"
        ),
        config_resolver=config_resolver_of(app_state),
        event_stream_hub=app_state.slice(CommunicationStateSlice).event_stream_hub,
        interrupt_store=app_state.slice(CommunicationStateSlice).interrupt_store,
        external_api_runtime=external_api_runtime,
        forge_tools_runtime=forge_tools_runtime,
        chat_tools_runtime=chat_tools_runtime,
        brain_tool_factory_provider=boot_brain_tool_factory_provider(app_state),
        knowledge_tool_factory_provider=boot_knowledge_tool_factory_provider(app_state),
        docs_tool_factory_provider=boot_docs_tool_factory_provider(app_state),
        research_tool_factory_provider=boot_research_tool_factory_provider(app_state),
        structure_map_tool_factory_provider=(
            boot_structure_map_tool_factory_provider(app_state)
        ),
        flight_recorder_sink=flight_recorder_sink,
        steering_inbox=boot_steering_inbox(app_state),
        stagnation_detector=create_stagnation_detector(app_state.config.stagnation),
        step_classifier=step_classifier,
        compaction_callback=_build_compaction_callback(app_state, provider),
        recovery_strategy=_build_recovery_strategy(app_state),
        openhands_loop_config=await build_openhands_loop_config(app_state),
        openhands_loop_deps=await build_openhands_loop_deps_or_none(app_state),
        auto_loop_config=await build_auto_loop_config_or_none(app_state),
        clock=app_state.clock,
    )


def _build_recovery_strategy(app_state: AppState) -> RecoveryStrategy:
    """Wire the configured crash-recovery strategy with its persistence deps.

    The checkpoint strategy needs a ``CheckpointRepository`` +
    ``HeartbeatRepository`` from the active backend; supply them, with the
    operator-tunable ``CheckpointConfig`` from
    ``config.recovery.checkpoint``, when persistence is connected so an
    operator selecting ``recovery.strategy = checkpoint`` gets a working,
    correctly-tuned strategy instead of a boot-time
    ``RecoveryConfigError``. The fail-reassign default ignores these deps.

    Returns:
        The recovery strategy for the boot ``AgentEngine``.
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None or not getattr(backend, "is_connected", False):
        return build_recovery_strategy(app_state.config.recovery)
    return build_recovery_strategy(
        app_state.config.recovery,
        checkpoint_repo=backend.checkpoints,
        heartbeat_repo=backend.heartbeats,
        checkpoint_config=app_state.config.recovery.checkpoint,
    )
