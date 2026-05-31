# module-kind: orchestrator
"""Provider-present switch: build the boot runtime services.

This is the construction site for the agent runtime. With a provider
configured it assembles ONE boot-time :class:`AgentEngine` (LLM +
sandboxed tools + memory, governed by the SecOps safety spine) and
shares that single engine between two consumers:

* an :class:`AgentEngineExecutionService` (the worker-callable execute
  seam), and
* a :class:`~synthorg.engine.coordination.service.MultiAgentCoordinator`
  built via :func:`~synthorg.engine.coordination.factory.build_coordinator`,
  whose :class:`~synthorg.engine.parallel.ParallelExecutor` runs sub-agents
  on the same engine.

With no provider it returns a :class:`NoProviderExecutionService` and a
``None`` coordinator, so the execute seam fails loudly and
``/coordinate`` honestly 503s instead of silently walking status
labels.

The same builder serves the boot install and the setup-reinit
rebuild, so configuring a provider brings the whole runtime (worker
execution AND multi-agent coordination) online without a process
restart.
"""

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from synthorg._core.features import require_service
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.baseline_store import BaselineStore
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.state import BudgetStateSlice, cost_tracker_of
from synthorg.client.state import client_simulation_state_of, has_simulation_runtime
from synthorg.communication.state import CommunicationStateSlice
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ToolCategory
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.coordination.factory import build_coordinator
from synthorg.engine.flight_recording import FlightRecorderSink
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.pipeline.factory import build_work_pipeline
from synthorg.engine.routing.scorer import AgentTaskScorer, RoutingScorerConfig
from synthorg.engine.routing_policy import build_stakes_router
from synthorg.engine.state import task_engine_of
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.git_worktree import PlannerWorktreeStrategy
from synthorg.engine.workspace.state import (
    WorkspaceStateSlice,
    agent_workspace_root_of,
)
from synthorg.hr.state import HrStateSlice, agent_registry_of
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
from synthorg.persistence.state import persistence_of
from synthorg.providers.state import has_active_provider, provider_registry_of
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.security.redteam.builder import (
    RedTeamRuntime,
    RedTeamToolSeed,
    build_red_team_runtime,
    build_red_team_tool_seed,
)
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import resolve_init_int
from synthorg.settings.state import config_resolver_of
from synthorg.tools.base import BaseTool
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.factory import (
    build_sandbox_backends,
    resolve_sandbox_for_category,
)
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.workers._agent_engine_collaborators import (
    boot_steering_inbox,
    build_boot_flight_recorder_sink,
)
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
    WorkerExecutionService,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from synthorg.api.state import AppState
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.pipeline.protocol import WorkPipeline
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
_GIT_TIMEOUT_NS: str = "tools"
_GIT_TIMEOUT_KEY: str = "git_command_timeout_seconds"
_DECOMPOSITION_NS: str = "coordination"
_DECOMPOSITION_KEY: str = "decomposition_model"
_ROUTING_POLICY_KEY: str = "routing_policy"
_LEAF_THRESHOLD_KEY: str = "leaf_subtask_threshold"
_BASELINE_WINDOW_KEY: str = "baseline_window_size"


def _resolve_baseline_window_size() -> int:
    """Resolve ``budget.baseline_window_size`` at boot.

    Cat-2 boot knob (``read_only_post_init``): the ``BaselineStore``
    sliding window is sized once at construction, so the value is
    sourced env > registered default via the bootstrap resolver (a
    runtime change requires a restart).

    Returns:
        The resolved baseline sliding-window size.
    """
    return resolve_init_int(SettingNamespace.BUDGET, _BASELINE_WINDOW_KEY)


def _construct_coordination_collector(
    app_state: AppState,
) -> CoordinationMetricsCollector | None:
    """Build the shared coordination-metrics collector, or ``None``.

    Requires a live ``CostTracker`` (the collector's only non-optional
    dependency). Without one - the empty/degraded path - no collector
    is built and the metrics pipeline stays a no-op, mirroring the
    ``_construct_agent_engine`` optional-dependency guards. The single
    instance returned is threaded into both the single-agent
    ``AgentEngine`` and the multi-agent coordinator so one
    ``BaselineStore`` accumulates the single-agent baselines the
    multi-agent metrics compare against.

    Returns:
        The shared ``CoordinationMetricsCollector``, or ``None`` when no
        ``CostTracker`` is wired (empty / degraded path).
    """
    if app_state.slice(BudgetStateSlice).cost_tracker is None:
        return None
    baseline_store = BaselineStore(window_size=_resolve_baseline_window_size())
    return CoordinationMetricsCollector(
        config=app_state.config.coordination_metrics,
        cost_tracker=cost_tracker_of(app_state),
        message_bus=app_state.slice(CommunicationStateSlice).message_bus,
        baseline_store=baseline_store,
        metrics_store=app_state.slice(CoordinationStateSlice).metrics_store,
        clock=app_state.clock,
    )


class RuntimeServices(NamedTuple):
    """The runtime services built behind the provider switch.

    INVARIANT (enforced by construction in :func:`build_runtime_services`,
    not by the type): when ``coordinator`` is not ``None`` it and
    ``worker_execution_service`` share the *same* boot
    :class:`AgentEngine` instance, so worker tasks and coordinator
    sub-agents observe one interrupt store, event-stream hub, and clock
    seam. The ``work_pipeline`` (when not ``None``) holds those very
    ``worker_execution_service`` and ``coordinator`` instances plus a
    single shared :class:`AgentTaskScorer`, so solo and team routing
    never diverge. A divergent engine would split agent state silently;
    ``tests/unit/workers/test_runtime_builder.py`` asserts the identity.
    ``coordinator`` and ``work_pipeline`` are ``None`` only in the
    empty-company (no-provider) case, where ``worker_execution_service``
    is a :class:`NoProviderExecutionService`; ``work_pipeline`` is also
    ``None`` when no intake runtime is wired (no work entry path).
    ``red_team_runtime`` is ``None`` when the adversarial gate is
    disabled (default) OR when no provider is configured.
    """

    worker_execution_service: WorkerExecutionService
    coordinator: MultiAgentCoordinator | None
    work_pipeline: WorkPipeline | None
    red_team_runtime: RedTeamRuntime | None = None
    vision_gate: VisionVerifierGate | None = None


def _select_active_provider(
    app_state: AppState,
) -> tuple[ProviderRegistry, tuple[str, ...]] | None:
    """Resolve the active provider registry, or ``None`` for empty-company.

    Logs the empty-company path and the unsupported multi-provider
    fan-in so the boot decision is observable.

    Returns:
        A ``(registry, provider_names)`` pair, or ``None`` for the
        empty-company (no usable provider) path.
    """
    security = app_state.config.security
    if not has_active_provider(app_state):
        logger.info(
            API_APP_STARTUP,
            service="runtime_services",
            mode="no_provider",
            note="empty company -- task execution rejected at the seam",
            security_enabled=security.enabled,
            security_enforcement_mode=security.enforcement_mode.value,
        )
        return None

    registry = provider_registry_of(app_state)
    names = registry.list_providers()
    if not names:
        logger.info(
            API_APP_STARTUP,
            service="runtime_services",
            mode="no_provider",
            note="provider registry present but empty",
            security_enabled=security.enabled,
            security_enforcement_mode=security.enforcement_mode.value,
        )
        return None
    if len(names) > 1:
        logger.warning(
            API_APP_STARTUP,
            service="runtime_services",
            note=(
                "multiple providers registered; the boot AgentEngine "
                "runs every agent against the first provider -- "
                "per-task multi-provider routing is not yet implemented"
            ),
            selected_provider=names[0],
            providers=list(names),
        )
    return registry, names


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
    except Exception as exc:
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
    except Exception as exc:
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

    A single instance is shared by the worker execution service and the
    coordinator's parallel executor so both consumers observe the same
    interrupt store, event stream hub, and clock seam. The same
    ``coordination_metrics_collector`` is shared too, so single-agent
    runs accumulate the baselines the multi-agent metrics compare
    against.

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
        flight_recorder_sink=flight_recorder_sink,
        steering_inbox=boot_steering_inbox(app_state),
        clock=app_state.clock,
    )


async def _build_workspace_strategy(
    app_state: AppState,
) -> tuple[PlannerWorktreeStrategy, WorkspaceIsolationConfig]:
    """Build the git-worktree workspace isolation strategy + config.

    The strategy operates on the workspace slice's
    ``agent_workspace_root`` (the same directory the worker runtime's
    sandbox tools use). Git
    subprocess invocations are bounded by the operator-tuned
    ``tools.git_command_timeout_seconds`` so a hung worktree command
    cannot stall a coordination wave. Construction (here, at boot) never
    touches git; a real repository is only required later, when a
    coordination wave first invokes ``workspace_service.setup_group()``
    during dispatch, and only when ``enable_workspace_isolation`` is set
    and the wave has multiple subtasks.

    Returns:
        A ``(strategy, config)`` pair: the git-worktree isolation
        strategy and its workspace-isolation config.
    """
    ws_config = WorkspaceIsolationConfig()
    git_timeout = await config_resolver_of(app_state).get_float(
        _GIT_TIMEOUT_NS,
        _GIT_TIMEOUT_KEY,
    )
    strategy = PlannerWorktreeStrategy(
        config=ws_config.planner_worktrees,
        repo_root=agent_workspace_root_of(app_state),
        cmd_timeout=git_timeout,
        clock=app_state.clock,
    )
    return strategy, ws_config


async def _resolve_routing_scorer_config(
    app_state: AppState,
) -> RoutingScorerConfig | None:
    """Project routing-scorer weights out of the engine bridge config.

    Fail-open: a bridge-resolution failure (missing setting, validation
    error, persistence flake) or a projection failure keeps the
    coordinator buildable by returning ``None`` so the factory falls
    back to ``task_assignment_config.min_score``. Mirrors the fail-open
    pattern used by ``auto_create_template_agents._resolve_matcher_config``
    and ``post_setup_reinit``. The resolve and projection stages are
    caught separately so the log says which one failed (a persistent
    config bug vs a transient resolver flake are diagnosed differently).

    Returns:
        The projected ``RoutingScorerConfig``, or ``None`` (fail-open) so
        the factory falls back to ``task_assignment_config.min_score``.
    """
    try:
        bridge = await config_resolver_of(app_state).get_engine_bridge_config()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="coordinator",
            context="routing_scorer_config_resolve",
            note="engine bridge config unavailable; using scorer defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    try:
        return RoutingScorerConfig.from_bridge_config(bridge)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="coordinator",
            context="routing_scorer_config_projection",
            note="scorer config projection failed; using scorer defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _build_runtime_coordinator(
    app_state: AppState,
    engine: AgentEngine,
    provider: CompletionProvider,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
) -> tuple[MultiAgentCoordinator, AgentTaskScorer, str]:
    """Build the coordinator and the shared scorer + decomposition model.

    Resolves the operator-tuned decomposition model and routing-scorer
    weights, wires real git-worktree workspace isolation, then delegates
    to the unit-tested :func:`build_coordinator` factory. The three
    resolution steps are independent, so they run concurrently under a
    ``TaskGroup`` to keep boot latency down (structured concurrency: any
    failure cancels the siblings and propagates). The ``AgentTaskScorer``
    is constructed here and injected into the coordinator so the work
    pipeline's solo-path selection can share the very same instance
    (one routing surface, no divergence). The resolved decomposition
    model is returned so the ``llm-judged`` routing policy reuses it.

    Returns:
        A ``(coordinator, scorer, decomposition_model)`` triple sharing
        the boot engine and a single ``AgentTaskScorer``.
    """
    try:
        async with asyncio.TaskGroup() as tg:
            model_task = tg.create_task(
                config_resolver_of(app_state).get_str(
                    _DECOMPOSITION_NS,
                    _DECOMPOSITION_KEY,
                )
            )
            scorer_task = tg.create_task(_resolve_routing_scorer_config(app_state))
            workspace_task = tg.create_task(_build_workspace_strategy(app_state))
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="coordinator",
            context="resolve_failed",
            note="decomposition / routing-scorer / workspace config resolve failed",
        )
        raise
    decomposition_model = model_task.result()
    routing_scorer_config = scorer_task.result()
    workspace_strategy, workspace_config = workspace_task.result()
    performance_tracker = app_state.slice(HrStateSlice).performance_tracker
    if routing_scorer_config is None:
        scorer = AgentTaskScorer(min_score=app_state.config.task_assignment.min_score)
    else:
        scorer = AgentTaskScorer(config=routing_scorer_config)
    project_workspace_service = app_state.slice(
        WorkspaceStateSlice
    ).project_workspace_service
    git_backend = (
        project_workspace_service.git_backend
        if project_workspace_service is not None
        else None
    )
    # ``AgentEngineExecutionService`` provisions the per-project workspace
    # lazily on first task; bare construction (no service) keeps the
    # persistence-less dev paths working as before.
    coordinator = build_coordinator(
        config=app_state.config.coordination,
        engine=engine,
        task_assignment_config=app_state.config.task_assignment,
        provider=provider,
        decomposition_model=decomposition_model,
        task_engine=task_engine_of(app_state),
        workspace_strategy=workspace_strategy,
        workspace_config=workspace_config,
        project_workspace_service=project_workspace_service,
        git_backend=git_backend,
        performance_tracker=performance_tracker,
        routing_scorer_config=routing_scorer_config,
        coordination_metrics_collector=coordination_metrics_collector,
        scorer=scorer,
    )
    logger.info(
        API_APP_STARTUP,
        service="coordinator",
        mode="multi_agent",
        decomposition_model=decomposition_model,
        topology=app_state.config.coordination.topology.value,
    )
    return coordinator, scorer, decomposition_model


async def _build_runtime_work_pipeline(  # noqa: PLR0913 -- keyword-only DI
    app_state: AppState,
    *,
    scorer: AgentTaskScorer,
    coordinator: MultiAgentCoordinator,
    worker_execution_service: WorkerExecutionService,
    provider: CompletionProvider,
    decomposition_model: str,
) -> WorkPipeline | None:
    """Build the work pipeline spine, or ``None`` when no intake is wired.

    The spine consumes the boot ``IntakeEngine`` wired by the
    client-simulation runtime (the only work-entry path online today);
    without it there is no intake stage, so the pipeline stays
    unconfigured and ``/`` work routing honestly reports unavailability
    rather than silently dropping work. The solo-vs-team routing policy
    discriminator and leaf threshold are resolved at boot so the
    setting-to-startup trace holds.

    Returns:
        The wired ``WorkPipeline``, or ``None`` when no intake runtime is
        available (no work-entry path).
    """
    if not has_simulation_runtime(app_state):
        logger.info(
            API_APP_STARTUP,
            service="work_pipeline",
            mode="disabled",
            note="no intake runtime wired; work spine unavailable",
        )
        return None
    intake_engine = client_simulation_state_of(app_state).intake_engine
    if intake_engine is None:
        logger.info(
            API_APP_STARTUP,
            service="work_pipeline",
            mode="disabled",
            note="simulation runtime present but intake engine unset",
        )
        return None
    routing_policy = await config_resolver_of(app_state).get_str(
        _DECOMPOSITION_NS,
        _ROUTING_POLICY_KEY,
    )
    leaf_threshold = await config_resolver_of(app_state).get_int(
        _DECOMPOSITION_NS,
        _LEAF_THRESHOLD_KEY,
    )
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    return build_work_pipeline(
        intake_engine=intake_engine,
        task_engine=task_engine_of(app_state),
        project_repository=persistence_of(app_state).projects,
        scorer=scorer,
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
        agent_registry=agent_registry_of(app_state),
        routing_discriminator=routing_policy,
        leaf_threshold=leaf_threshold,
        provider=provider,
        decomposition_model=decomposition_model,
        cost_tracker=cost_tracker,
        clock=app_state.clock,
    )


async def build_runtime_services(
    app_state: AppState,
    *,
    workspace_root: Path,
) -> RuntimeServices:
    """Return the runtime services for the current provider state.

    Args:
        app_state: Live application state (provider registry, task
            engine, approval store, security config, ...).
        workspace_root: Absolute filesystem root the agent's
            file-system / sandbox tools operate within. Resolved by the
            startup site (env-aware) and carried on ``AppState`` so the
            re-init path rebuilds against the same directory.

    Returns:
        ``RuntimeServices`` with an ``AgentEngineExecutionService``, a
        live ``MultiAgentCoordinator``, and the ``WorkPipeline`` spine
        (all sharing one ``AgentEngine`` and one ``AgentTaskScorer``)
        when a provider is registered and an intake runtime is wired;
        otherwise a ``NoProviderExecutionService`` and ``None`` for the
        coordinator and the work pipeline.
    """
    selected = _select_active_provider(app_state)
    if selected is None:
        return RuntimeServices(
            worker_execution_service=NoProviderExecutionService(),
            coordinator=None,
            work_pipeline=None,
            vision_gate=_build_vision_gate_or_none(
                app_state=app_state,
                workspace_root=workspace_root,
                provider=None,
            ),
        )
    registry, names = selected
    provider = registry.get(names[0])

    red_team_seed = build_red_team_tool_seed(
        config=app_state.config.security.red_team,
    )
    tool_registry, tool_count, sandbox_backends = await _build_tool_registry(
        app_state,
        workspace_root,
        extra_tools=red_team_seed.extra_tools,
    )
    coordination_metrics_collector = _construct_coordination_collector(app_state)
    external_api_runtime = await _build_external_api_runtime(app_state)
    flight_recorder_sink = await build_boot_flight_recorder_sink(app_state)
    engine = _construct_agent_engine(
        app_state,
        provider,
        registry,
        tool_registry,
        coordination_metrics_collector,
        external_api_runtime,
        active_provider_name=names[0],
        flight_recorder_sink=flight_recorder_sink,
    )
    autonomy_resolver = AutonomyResolver(
        registry=ActionTypeRegistry(),
        config=app_state.config.config.autonomy,
    )
    coordinator, scorer, decomposition_model = await _build_runtime_coordinator(
        app_state,
        engine,
        provider,
        coordination_metrics_collector,
    )
    security = app_state.config.security
    logger.info(
        API_APP_STARTUP,
        service="runtime_services",
        mode="agent_engine",
        provider=names[0],
        tool_count=tool_count,
        security_enabled=security.enabled,
        security_enforcement_mode=security.enforcement_mode.value,
    )
    # The env runner provisions the declaration into the same backend the
    # build/test tool categories resolve to (not necessarily Docker), so
    # provisioning matches what the agent's code-execution tools use.
    environment_runner_backend = resolve_sandbox_for_category(
        config=app_state.config.sandboxing,
        backends=sandbox_backends,
        category=ToolCategory.CODE_EXECUTION,
    )
    workspace_slice = app_state.slice(WorkspaceStateSlice)
    worker_execution_service = AgentEngineExecutionService(
        engine=engine,
        task_engine=task_engine_of(app_state),
        agent_registry=agent_registry_of(app_state),
        autonomy_resolver=autonomy_resolver,
        # Release the lifecycle owner on the SAME backend the code-execution
        # tools resolve to (not hardwired docker): if code execution maps to
        # subprocess, a docker-pinned release would target the wrong backend
        # and skip owner cleanup on the one that actually held the container.
        sandbox_backend=environment_runner_backend,
        lifecycle_strategy_kind=(app_state.config.sandboxing.docker.lifecycle.strategy),
        project_workspace_service=workspace_slice.project_workspace_service,
        environment_service=workspace_slice.environment_service,
        environment_runner_backend=environment_runner_backend,
    )
    work_pipeline = await _build_runtime_work_pipeline(
        app_state,
        scorer=scorer,
        coordinator=coordinator,
        worker_execution_service=worker_execution_service,
        provider=provider,
        decomposition_model=decomposition_model,
    )
    red_team_runtime = _build_red_team_runtime_or_none(
        app_state=app_state,
        engine=engine,
        provider_name=names[0],
        seed=red_team_seed,
    )
    vision_gate = _build_vision_gate_or_none(
        app_state=app_state,
        workspace_root=workspace_root,
        provider=provider,
    )
    logger.info(
        API_APP_STARTUP,
        service="runtime_services",
        mode="agent_engine_built",
        coordinator_wired=coordinator is not None,
        work_pipeline_wired=work_pipeline is not None,
        red_team_wired=red_team_runtime is not None,
        vision_gate_wired=vision_gate is not None,
        security_enabled=security.enabled,
        security_enforcement_mode=security.enforcement_mode.value,
    )
    return RuntimeServices(
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
        work_pipeline=work_pipeline,
        red_team_runtime=red_team_runtime,
        vision_gate=vision_gate,
    )


def _build_red_team_runtime_or_none(
    *,
    app_state: AppState,
    engine: AgentEngine,
    provider_name: str,
    seed: RedTeamToolSeed,
) -> RedTeamRuntime | None:
    """Construct the red-team runtime when the gate is enabled.

    Pulls :class:`RedTeamConfig` from ``app_state.config.security.red_team``
    and pins the red-team agent's :class:`ModelConfig` to the company's
    active provider with the vendor-agnostic ``example-medium-001``
    model id; operators override via the post-init swap path. The
    ``seed`` parameter carries the per-boot
    :class:`InMemoryRedTeamReportRepository` and
    :class:`SubmitRedTeamReportTool` already registered on the engine's
    tool registry, so the runtime shares those instances rather than
    constructing fresh ones.

    Returns:
        The ``RedTeamRuntime`` when the gate is enabled, otherwise
        ``None``.
    """
    from synthorg.core.agent import ModelConfig  # noqa: PLC0415

    return build_red_team_runtime(
        config=app_state.config.security.red_team,
        engine=engine,
        model=ModelConfig(
            provider=provider_name,
            model_id="example-medium-001",
        ),
        seed=seed,
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
    from synthorg.security.visionverify.errors import (  # noqa: PLC0415
        VisionVerifyConfigError,
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
    except VisionVerifyConfigError as exc:
        logger.warning(
            API_APP_STARTUP,
            service="runtime_services",
            note="vision verifier gate disabled: configuration incomplete",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
