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
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.agent_state_recording import AgentStateRepositoryProvider
from synthorg.engine.artifacts.baseline_scope import workspace_run_probe
from synthorg.engine.flight_recording import FlightRecorderSink
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.recovery import RecoveryStrategy
from synthorg.engine.recovery_factory import build_recovery_strategy
from synthorg.engine.stagnation import create_stagnation_detector
from synthorg.engine.state import EngineStateSlice, task_engine_of
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.integrations.state import IntegrationsStateSlice, connection_catalog_of
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.evolution import EVOLUTION_PROPOSER_MODEL_UNSET
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.background_job_protocol import BackgroundJobRepository
from synthorg.persistence.memory_protocol import OrgFactRepository
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.persistence.state import (
    PersistenceStateSlice,
    code_execution_records_of,
    project_repository_of,
)
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.providers.model_binding import resolve_bound_completion
from synthorg.security.config import SecurityConfig
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of
from synthorg.tools.base import BaseTool
from synthorg.tools.ceilings import ToolCeilings
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.factory import (
    build_sandbox_backends,
    merge_secure_backend_defaults,
)
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy
from synthorg.tools.web.fetch_types import WebFetchRungs, WebToolsWiring
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
from synthorg.workers._capability_policy_wiring import build_capability_policy
from synthorg.workers._classification_assembly import build_classification
from synthorg.workers._image_provider_wiring import build_image_provider_or_none
from synthorg.workers._memory_assembly import (
    MemoryInjectionResolver,
    resolved_procedural_config,
    wiki_exporter_or_none,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.evolution.service import EvolutionService
    from synthorg.engine.quality.classifier import StepQualityClassifier
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"
_TOOLS_NS: str = "tools"
_GIT_LOG_MAX_COUNT_KEY: str = "git_log_max_count"
_CODE_RUNNER_OUTPUT_TAIL_KEY: str = "code_runner_output_tail_limit"
_BACKGROUND_MAX_CONCURRENT_JOBS_KEY: str = (
    "shell_command_background_max_concurrent_jobs"
)
_BACKGROUND_OUTPUT_BYTE_CAP_KEY: str = "shell_command_background_output_byte_cap"
_EXTERNAL_API_NS: str = SettingNamespace.EXTERNAL_API.value


async def _build_tool_registry(
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
    background_max_concurrent_jobs = await resolver.get_int(
        _TOOLS_NS, _BACKGROUND_MAX_CONCURRENT_JOBS_KEY
    )
    background_output_byte_cap = await resolver.get_int(
        _TOOLS_NS, _BACKGROUND_OUTPUT_BYTE_CAP_KEY
    )
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

    browser_settings = await resolve_browser_settings(config_resolver_of(app_state))
    desktop_settings = await resolve_desktop_settings(config_resolver_of(app_state))
    background_job_repo = _background_job_repo_or_none(app_state)
    background_jobs = (
        BackgroundJobRegistry(background_job_repo, clock=app_state.clock)
        if background_job_repo is not None
        else None
    )
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
    if background_jobs is not None and isinstance(
        lifecycle_strategy, PerAgentStrategy | PerTaskStrategy
    ):
        # Binds after both the strategy and the Docker backend that owns
        # ``pin_check`` exist, breaking the construction-order cycle
        # documented on ``PerAgentStrategy.bind_pin_check`` /
        # ``PerTaskStrategy.bind_pin_check``: the strategy has to exist
        # before ``build_sandbox_backends`` can construct the sandbox,
        # and ``pin_check`` is a bound method of that sandbox. Safe
        # because grace/idle expiry only ever reads ``pin_check`` for a
        # container already acquired, and none has been yet.
        docker_backend = sandbox_backends.get("docker")
        if isinstance(docker_backend, DockerSandbox):
            lifecycle_strategy.bind_pin_check(docker_backend.pin_check)
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
    if not app_state.config.evolution.enabled:
        return None
    from synthorg.engine.evolution.factory import (  # noqa: PLC0415
        build_evolution_service,
    )
    from synthorg.meta.state import evolution_outcome_store_of  # noqa: PLC0415
    from synthorg.versioning import VersioningService  # noqa: PLC0415

    hr = app_state.slice(HrStateSlice)
    persistence = app_state.slice(PersistenceStateSlice).backend
    if (
        persistence is None
        or not getattr(persistence, "is_connected", False)
        or not hasattr(persistence, "identity_versions")
        or hr.agent_registry is None
        or hr.performance_tracker is None
    ):
        return None
    # Evolution rewrites agent identities, so what analyses them is the
    # operator's explicit choice, never a borrowed connection.
    proposer = await resolve_bound_completion(
        app_state,
        namespace="engine",
        key="evolution_proposer_model",
        unset_event=EVOLUTION_PROPOSER_MODEL_UNSET,
        subject="evolution proposer",
    )
    try:
        service = build_evolution_service(
            app_state.config.evolution,
            registry=hr.agent_registry,
            versioning=VersioningService(persistence.identity_versions),
            tracker=hr.performance_tracker,
            memory_backend=app_state.slice(MemoryStateSlice).backend,
            proposer_binding=proposer,
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


def _live_security(app_state: AppState) -> SecurityConfig:
    """Return the security config in force, falling back to the boot one.

    The live holder is swapped by the security-bridge subscriber on every
    watched write. Before it has been populated there is nothing to prefer,
    and the boot config is the same value the holder was seeded with.

    Returns:
        The security configuration a rebuild should compose against.
    """
    return app_state.security_runtime_config.current or app_state.config.security


def _org_fact_store_or_none(app_state: AppState) -> OrgFactRepository | None:
    """Resolve the org-fact store, or ``None`` before persistence connects.

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    return None if persistence is None else persistence.org_facts


def agent_state_repository_provider(
    app_state: AppState,
) -> AgentStateRepositoryProvider:
    """Return a provider reading the live agent-state repository.

    Public because two boot paths claim live rows against it: the agent engine
    for every dispatched task, and the coordinator's planning session, which
    runs as a staffed agent without going through the engine. Built once here
    so both read the same slice through the same connectedness check.

    Returns:
        A zero-arg callable returning the current repository, or ``None``
        while persistence is unconnected.
    """

    def _provider() -> AgentStateRepository | None:
        persistence = app_state.slice(PersistenceStateSlice).backend
        if persistence is None or not persistence.is_connected:
            return None
        return persistence.agent_states

    return _provider


def _parked_context_repo_or_none(app_state: AppState) -> ParkedContextRepository | None:
    """Resolve the parked-context store, or ``None`` before persistence connects.

    The engine builds its own ``ApprovalGate`` whenever boot did not inject
    one, and a gate without this repository cannot park at all: it refuses
    the park rather than reporting PARKED over nothing, so the escalation
    lands as ERROR. Handing it the same repository the boot gate uses is
    what makes that fallback a working exit instead of a dead end.

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None or not persistence.is_connected:
        return None
    return persistence.parked_contexts


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


def _background_job_repo_or_none(
    app_state: AppState,
) -> BackgroundJobRepository | None:
    """Resolve the background-job store, or ``None`` before persistence connects.

    A Docker backend built without this repository cannot start, poll,
    read or cancel a background job at all (``start_background`` refuses
    with ``SandboxBackgroundUnsupportedError``): the feature is entirely
    persistence-backed, unlike the container tracker which merely
    degrades to an in-memory dict.

    Returns:
        The repository, or ``None``.
    """
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None or not persistence.is_connected:
        return None
    return persistence.background_jobs


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
    connection_tool_runtimes: ConnectionToolRuntimes | None = None,
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
        # The SAME instance selection judges against, so a task assigned by
        # the ladder always clears here. This is the last line for a pair
        # selection never saw: a hand-assigned task, or a reassignment.
        capability=await build_capability_policy(app_state),
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
        parked_context_repo=_parked_context_repo_or_none(app_state),
        review_gate=app_state.slice(ApprovalStateSlice).review_gate,
        review_pipeline=await _build_auto_review_pipeline_or_none(app_state),
        # The engine holds no workspace root, so the layout knowledge stays
        # here and it receives the question it can ask: did this project
        # produce what its task declared. Bound to the same root the agent's
        # file tools write through, so the check reads what the run wrote.
        run_probe=workspace_run_probe(agent_workspace_root_of(app_state)),
        clarification_enabled=await config_resolver_of(app_state).get_bool(
            "engine", "clarification_enabled"
        ),
        scoping_enabled=await config_resolver_of(app_state).get_bool(
            "engine", "scoping_enabled"
        ),
        cost_forecast_repo=app_state.slice(BudgetStateSlice).cost_forecast_repo,
        approval_gate=app_state.slice(ApprovalStateSlice).gate,
        # Read from the LIVE security config, not the boot snapshot: the mode
        # is an operator setting, and a rebuild triggered by that setting has
        # to see the value it was triggered by.
        mcp_self_consumer=build_mcp_self_consumer(
            _live_security(app_state).mcp_self_consumer,
            app_state,
        ),
        security_config=app_state.config.security,
        # Live security config the per-request interceptor reads, so an
        # operator toggle of the security.* flags applies without a restart.
        security_config_provider=lambda: app_state.security_runtime_config.current,
        audit_log=app_state.slice(SecurityStateSlice).audit_log,
        memory_backend=app_state.slice(MemoryStateSlice).backend,
        # A resolver, not a strategy: memory can be wired after the engine is
        # built (an embedder that was unreachable at boot and is reachable
        # now), and a captured strategy would leave those agents with no
        # recall until the process restarted.
        memory_injection_strategy_provider=MemoryInjectionResolver(
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
        connection_tool_runtimes=connection_tool_runtimes,
        brain_tool_factory_provider=boot_brain_tool_factory_provider(app_state),
        knowledge_tool_factory_provider=boot_knowledge_tool_factory_provider(app_state),
        docs_tool_factory_provider=boot_docs_tool_factory_provider(app_state),
        research_tool_factory_provider=boot_research_tool_factory_provider(app_state),
        structure_map_tool_factory_provider=(
            boot_structure_map_tool_factory_provider(app_state)
        ),
        flight_recorder_sink=flight_recorder_sink,
        # A provider, not the repository: a run can start before persistence
        # is connected, and a captured ``None`` would leave that agent absent
        # from the live view for the life of the process.
        agent_state_repository_provider=agent_state_repository_provider(app_state),
        steering_inbox=boot_steering_inbox(app_state),
        stagnation_detector=create_stagnation_detector(app_state.config.stagnation),
        step_classifier=step_classifier,
        compaction_callback=_build_compaction_callback(app_state, provider),
        recovery_strategy=_build_recovery_strategy(app_state),
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
