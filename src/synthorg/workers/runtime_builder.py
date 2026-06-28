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

Engine-side construction helpers live in
:mod:`synthorg.workers._engine_assembly`; coordination-side helpers in
:mod:`synthorg.workers._coordinator_assembly`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from synthorg.api.state import AppState
from synthorg.budget.baseline_store import BaselineStore
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.state import BudgetStateSlice, cost_tracker_of
from synthorg.communication.state import CommunicationStateSlice
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import PersistenceError
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.health import (
    HealthJudge,
    HealthMonitoringPipeline,
    TriageFilter,
)
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.quality.classifier import RuleBasedStepClassifier
from synthorg.engine.state import task_engine_of
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.hr.state import agent_registry_of
from synthorg.integrations.state import provider_credential_catalog_of
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.workers import (
    WORKERS_ENGINE_BRIDGE_CONFIG_FALLBACK,
    WORKERS_RUNTIME_HOT_SWAP_FAILED,
    WORKERS_RUNTIME_RELOADED,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import has_active_provider, provider_registry_of
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.security.redteam.builder import (
    RedTeamRuntime,
    build_red_team_tool_seed,
)
from synthorg.security.visionverify.protocol import VisionVerifierGate
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import SettingsError
from synthorg.settings.mirrors import resolve_init_int
from synthorg.settings.state import config_resolver_of
from synthorg.tools.sandbox.factory import resolve_sandbox_for_category
from synthorg.workers._agent_engine_collaborators import (
    build_boot_flight_recorder_sink,
)
from synthorg.workers._coordinator_assembly import (
    _build_runtime_coordinator,
    _build_runtime_work_pipeline,
)
from synthorg.workers._engine_assembly import (
    _build_external_api_runtime,
    _build_mcp_bridge_tools,
    _build_tool_registry,
    _build_vision_gate_or_none,
    _construct_agent_engine,
)
from synthorg.workers._red_team_runtime import build_red_team_runtime_or_none
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
    WorkerExecutionService,
)

logger = get_logger(__name__)

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


def _build_health_runtime(
    app_state: AppState,
    *,
    quality_degradation_threshold: int,
) -> tuple[HealthMonitoringPipeline | None, Callable[[], Awaitable[bool]] | None]:
    """Build the post-run agent-health pipeline + its live enabled check.

    The pipeline composes the sensitive :class:`HealthJudge`, the
    conservative :class:`TriageFilter`, and the notification dispatcher as
    the escalation sink. Without a wired dispatcher there is nowhere to
    deliver escalations, so ``(None, None)`` is returned. The enabled
    check re-reads ``engine.health_monitoring_enabled`` per run so the
    monitor can be toggled without a restart.

    Args:
        app_state: The live application state.
        quality_degradation_threshold: Bridged
            ``engine.health_quality_degradation_threshold`` (minimum
            consecutive INCORRECT step signals before the judge escalates).

    Returns:
        A ``(pipeline, enabled_check)`` pair, or ``(None, None)`` when no
        notification dispatcher is wired.
    """
    dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
    if dispatcher is None:
        return None, None
    pipeline = HealthMonitoringPipeline(
        judge=HealthJudge(
            quality_degradation_threshold=quality_degradation_threshold,
        ),
        triage=TriageFilter(),
        notification_dispatcher=dispatcher,
    )
    resolver = config_resolver_of(app_state)

    async def _enabled() -> bool:
        return await resolver.get_bool(
            SettingNamespace.ENGINE, "health_monitoring_enabled"
        )

    return pipeline, _enabled


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
    # Bind the always-on credential catalog so ``connection_name`` provider
    # credentials resolve at call time. Boot can build the registry before the
    # catalog is wired (the catalog needs a connected persistence backend), so
    # we (re)bind here in the runtime path where both are guaranteed present.
    registry.bind_credential_catalog(provider_credential_catalog_of(app_state))
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
    mcp_bridge_tools = await _build_mcp_bridge_tools(app_state)
    tool_registry, tool_count, sandbox_backends = await _build_tool_registry(
        app_state,
        workspace_root,
        extra_tools=(*red_team_seed.extra_tools, *mcp_bridge_tools),
    )
    coordination_metrics_collector = _construct_coordination_collector(app_state)
    external_api_runtime = await _build_external_api_runtime(app_state)
    flight_recorder_sink = await build_boot_flight_recorder_sink(app_state)
    try:
        engine_bridge = await config_resolver_of(app_state).get_engine_bridge_config()
    except (SettingsError, PersistenceError, ValidationError, ValueError) as exc:
        # Tolerate only settings-resolution / backend-outage failures: a
        # missing or unparseable key, an out-of-range bridged value, or the
        # settings store being unreachable. Unexpected exceptions (wiring
        # bugs) propagate so broken classifier/health config never boots
        # silently on defaults.
        logger.warning(
            WORKERS_ENGINE_BRIDGE_CONFIG_FALLBACK,
            context="engine_bridge_resolve",
            note="engine bridge config unavailable; using classifier/health defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        engine_bridge = EngineBridgeConfig()
    step_classifier = RuleBasedStepClassifier(
        rule_matched_confidence=engine_bridge.classifier_rule_matched_confidence,
        fallback_confidence=engine_bridge.classifier_fallback_confidence,
    )
    engine = _construct_agent_engine(
        app_state,
        provider,
        registry,
        tool_registry,
        coordination_metrics_collector,
        external_api_runtime,
        active_provider_name=names[0],
        flight_recorder_sink=flight_recorder_sink,
        step_classifier=step_classifier,
        classification_detector_timeout_seconds=(
            engine_bridge.classification_detector_timeout_seconds
        ),
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
    health_pipeline, health_enabled = _build_health_runtime(
        app_state,
        quality_degradation_threshold=(
            engine_bridge.health_quality_degradation_threshold
        ),
    )
    worker_execution_service = AgentEngineExecutionService(
        engine=engine,
        task_engine=task_engine_of(app_state),
        agent_registry=agent_registry_of(app_state),
        autonomy_resolver=autonomy_resolver,
        health_pipeline=health_pipeline,
        health_enabled=health_enabled,
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
    red_team_runtime = build_red_team_runtime_or_none(
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


# Serialises the whole rebuild + swap so two concurrent reloads (rapid MCP
# installs, or a catalog install racing /setup/complete) cannot interleave
# their per-service swaps and leave AppState pairing a coordinator from one
# build with a worker-execution service from another. Module-level, mirroring
# the setup path's COMPLETE_LOCK; asyncio.Lock binds to the loop on first
# acquire, not at construction.
_RUNTIME_RELOAD_LOCK = asyncio.Lock()


async def reload_runtime_services(app_state: AppState) -> None:
    """Rebuild runtime services and hot-swap them into ``AppState``.

    Brings the agent runtime (worker execution service, multi-agent
    coordinator, work pipeline, and pipeline entry adapters) back online
    with the CURRENT config and tool set WITHOUT a process restart. Used
    after provider setup and after an MCP catalog install/uninstall so a
    newly bridged (or removed) external-MCP tool goes live for the next
    task without restarting the process.

    With no provider configured, ``build_runtime_services`` returns a
    ``None`` coordinator and work pipeline, so only the worker execution
    service is swapped (to a ``NoProviderExecutionService``); the
    coordinator and pipeline swaps are skipped.

    A module lock serialises the rebuild + swap so concurrent reloads
    cannot interleave their per-service swaps. The swap itself is atomic
    per service: an in-flight task holding the prior engine finishes on
    it; the next task picks up the rebuilt one. A failure midway leaves
    ``AppState`` partially swapped, so the partial state is logged; the
    next reload reapplies the full set and heals it.

    Raises:
        Exception: Propagated from ``build_runtime_services`` (or a swap)
            so the caller decides whether a failure is fatal (setup
            reinit) or best-effort (MCP reload).
    """
    from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
        wire_real_intake_entry,
        wire_real_objective_entry,
        wire_real_task_board_entry,
    )
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )

    async with _RUNTIME_RELOAD_LOCK:
        services = await build_runtime_services(
            app_state,
            workspace_root=agent_workspace_root_of(app_state),
        )
        try:
            app_state.swap_worker_execution_service(services.worker_execution_service)
            if services.coordinator is not None:
                app_state.swap_coordinator(services.coordinator)
            if services.work_pipeline is not None:
                app_state.swap_work_pipeline(services.work_pipeline)
            await wire_real_intake_entry(app_state, hot_swap=True)
            await wire_real_objective_entry(app_state, hot_swap=True)
            await wire_real_task_board_entry(app_state, hot_swap=True)
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                WORKERS_RUNTIME_HOT_SWAP_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
    logger.info(
        WORKERS_RUNTIME_RELOADED,
        coordinator_swapped=services.coordinator is not None,
        pipeline_swapped=services.work_pipeline is not None,
    )
