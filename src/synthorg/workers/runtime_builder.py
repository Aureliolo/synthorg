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
from pathlib import Path

from pydantic import ValidationError

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import PersistenceError
from synthorg.engine.completion_oracle.builder import (
    build_completion_oracle_tool_seed,
)
from synthorg.engine.errors import CoordinationConfigError
from synthorg.engine.quality.classifier import RuleBasedStepClassifier
from synthorg.engine.state import task_engine_of
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.hr.state import agent_registry_of
from synthorg.integrations.state import provider_credential_catalog_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.decomposition import DECOMPOSITION_MODEL_UNSET
from synthorg.observability.events.workers import (
    WORKERS_ENGINE_BRIDGE_CONFIG_FALLBACK,
    WORKERS_RUNTIME_HOT_SWAP_FAILED,
    WORKERS_RUNTIME_RELOADED,
)
from synthorg.persistence.state import project_repository_of
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import has_active_provider, provider_registry_of
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.security.redteam.builder import (
    build_red_team_tool_seed,
)
from synthorg.settings.bound_model import resolve_bound_model
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.settings.errors import SettingsError
from synthorg.settings.state import config_resolver_of
from synthorg.tools.sandbox.factory import resolve_sandbox_for_category
from synthorg.workers._agent_engine_collaborators import (
    build_boot_flight_recorder_sink,
)
from synthorg.workers._agent_tools_wiring import (
    build_chat_tools_runtime_or_none,
    build_forge_tools_runtime_or_none,
)
from synthorg.workers._completion_oracle_runtime import (
    attach_completion_oracle_gates,
    build_completion_oracle_runtime_or_none,
    resolve_completion_oracle_config,
)
from synthorg.workers._coordinator_assembly import (
    _DECOMPOSITION_KEY,
    _DECOMPOSITION_NS,
    _build_runtime_coordinator,
    _build_runtime_work_pipeline,
)
from synthorg.workers._engine_assembly import (
    _build_external_api_runtime,
    _build_tool_registry,
    _construct_agent_engine,
)
from synthorg.workers._mcp_bridge_wiring import build_mcp_bridge_tools
from synthorg.workers._red_team_runtime import build_red_team_runtime_or_none
from synthorg.workers._runtime_aux_wiring import (
    _build_health_runtime,
    _construct_coordination_collector,
)
from synthorg.workers._runtime_services import RuntimeServices
from synthorg.workers._vision_gate_wiring import build_vision_gate_or_none
from synthorg.workers._web_search_provider_wiring import (
    build_web_search_provider_or_none,
)
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)

logger = get_logger(__name__)


def _select_active_provider(app_state: AppState) -> ProviderRegistry | None:
    """Resolve the active provider registry, or ``None`` for empty-company.

    Logs the empty-company path and the multi-provider fan-in so the boot
    decision is observable.

    Returns:
        The registry, or ``None`` for the empty-company (nothing registered
        to dispatch on) path.
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
        logger.info(
            API_APP_STARTUP,
            service="runtime_services",
            note=(
                "multiple providers registered; each feature dispatches on its "
                "own configured (provider, model) pair, and stakes routing "
                "picks the cheapest model per tier per task"
            ),
            providers=list(names),
        )
    return registry


async def _no_active_provider_services(
    app_state: AppState,
    workspace_root: Path,
    *,
    oracle_enabled: bool,
) -> RuntimeServices:
    """Boot the no-provider mode: nothing registered to dispatch on.

    The deterministic build/test gate still attaches; only the
    provider-dependent runtimes stay off.

    Returns:
        No-provider ``RuntimeServices`` (``NoProviderExecutionService``,
        ``coordinator=None``, ``work_pipeline=None``).
    """
    return RuntimeServices(
        worker_execution_service=NoProviderExecutionService(),
        coordinator=None,
        work_pipeline=None,
        completion_oracle_enabled=oracle_enabled,
        vision_gate=await build_vision_gate_or_none(
            app_state=app_state,
            workspace_root=workspace_root,
        ),
    )


async def _degraded_no_coordinator(
    app_state: AppState,
    workspace_root: Path,
    *,
    oracle_enabled: bool,
    error: BaseException | None = None,
) -> RuntimeServices:
    """Boot the degraded no-coordinator mode: task execution rejected at the seam.

    Used when a provider is registered but ``coordination.decomposition_model``
    names no registered ``(provider, model)`` pair (e.g. a capability toggle
    rebuilt the coordinator mid-setup before the assignment landed). Reject
    task execution at the seam until a coordination pair is configured, instead
    of crashing the boot / reload. Setting it triggers a watched-key rebuild
    that succeeds, so this self-heals.

    Args:
        app_state: Live application state.
        workspace_root: Absolute filesystem root for the vision gate.
        oracle_enabled: Whether the completion oracle is enabled, so the
            deterministic build/test gate still attaches in degraded mode
            (it needs no coordinator, only the execution-record store).
        error: The caught config error, when reached from the coordinator
            build's late backstop rather than the cheap pre-check.

    Returns:
        Degraded ``RuntimeServices`` (``NoProviderExecutionService``,
        ``coordinator=None``, ``work_pipeline=None``).
    """
    error_fields = (
        {}
        if error is None
        else {
            "error_type": type(error).__name__,
            "error": safe_error_description(error),
        }
    )
    logger.warning(
        API_APP_STARTUP,
        service="runtime_services",
        mode="no_coordinator",
        note=(
            "provider registered but coordination.decomposition_model names no "
            "registered (provider, model) pair; task execution rejected at the "
            "seam until a coordination pair is configured"
        ),
        **error_fields,
    )
    return RuntimeServices(
        worker_execution_service=NoProviderExecutionService(),
        coordinator=None,
        work_pipeline=None,
        completion_oracle_enabled=oracle_enabled,
        vision_gate=await build_vision_gate_or_none(
            app_state=app_state,
            workspace_root=workspace_root,
        ),
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
    # Resolve the oracle config before any early return so the deterministic
    # build/test gate's enablement survives the no-provider and degraded paths;
    # only the peer-review runtime depends on an active provider.
    completion_oracle_config = await resolve_completion_oracle_config(app_state)
    registry = _select_active_provider(app_state)
    if registry is None:
        return await _no_active_provider_services(
            app_state,
            workspace_root,
            oracle_enabled=completion_oracle_config.enabled,
        )
    # Cheap pre-check before the expensive engine / MCP-bridge assembly: when
    # the coordination pair is unset the coordinator build would raise anyway,
    # so short-circuit to degraded mode here rather than tearing down and
    # reconnecting live MCP sessions only to discard the result. This keeps the
    # self-heal reload path (an unrelated watched-key write while the pair is
    # still blank) from churning healthy resources.
    #
    # That same pair also supplies the engine's connection. There is no shared
    # "default provider" to inherit: a provider is a registered connection with
    # its own credentials, endpoint and quota, so every dispatch names one.
    # Agents dispatch on their own bound pair through the registry; this client
    # is what the engine holds when no registry is wired at all, and taking it
    # from the one pair the runtime already requires means it is never a
    # connection nobody chose.
    decomposition = await resolve_bound_model(
        app_state,
        namespace=_DECOMPOSITION_NS,
        key=_DECOMPOSITION_KEY,
        unset_event=DECOMPOSITION_MODEL_UNSET,
    )
    if decomposition is None or decomposition.provider not in registry:
        return await _degraded_no_coordinator(
            app_state,
            workspace_root,
            oracle_enabled=completion_oracle_config.enabled,
        )
    provider = registry.get(decomposition.provider)

    red_team_seed = build_red_team_tool_seed(
        config=app_state.config.security.red_team,
    )
    completion_oracle_seed = build_completion_oracle_tool_seed(
        config=completion_oracle_config,
    )
    mcp_bridge_tools = await build_mcp_bridge_tools(app_state)
    # Resolve the native web-search provider once per boot and thread the single
    # instance into both the tool registry and the coordinator's planning grant,
    # rather than each assembly re-resolving settings and building its own.
    search_provider = await build_web_search_provider_or_none(app_state)
    tool_registry, tool_count, sandbox_backends = await _build_tool_registry(
        app_state,
        workspace_root,
        extra_tools=(
            *red_team_seed.extra_tools,
            *completion_oracle_seed.extra_tools,
            *mcp_bridge_tools,
        ),
        search_provider=search_provider,
    )
    coordination_metrics_collector = _construct_coordination_collector(app_state)
    external_api_runtime = await _build_external_api_runtime(app_state)
    forge_tools_runtime = await build_forge_tools_runtime_or_none(app_state)
    chat_tools_runtime = await build_chat_tools_runtime_or_none(app_state)
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
    engine = await _construct_agent_engine(
        app_state,
        provider,
        registry=registry,
        tool_registry=tool_registry,
        coordination_metrics_collector=coordination_metrics_collector,
        external_api_runtime=external_api_runtime,
        forge_tools_runtime=forge_tools_runtime,
        chat_tools_runtime=chat_tools_runtime,
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
    try:
        (
            coordinator,
            scorer,
            decomposition_provider,
            decomposition_model,
        ) = await _build_runtime_coordinator(
            app_state,
            engine,
            coordination_metrics_collector,
            search_provider=search_provider,
        )
    except CoordinationConfigError as exc:
        # Backstop for the case the pre-check cannot catch: the model was
        # cleared between the pre-check read and this eager build. Degrade to
        # the same no-coordinator mode the pre-check returns.
        return await _degraded_no_coordinator(
            app_state,
            workspace_root,
            oracle_enabled=completion_oracle_config.enabled,
            error=exc,
        )
    security = app_state.config.security
    logger.info(
        API_APP_STARTUP,
        service="runtime_services",
        mode="agent_engine",
        provider=decomposition.provider,
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
        project_repo=project_repository_of(app_state),
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
        provider=decomposition_provider,
        decomposition_model=decomposition_model,
    )
    red_team_runtime = await build_red_team_runtime_or_none(
        app_state=app_state,
        engine=engine,
        seed=red_team_seed,
    )
    completion_oracle_runtime = await build_completion_oracle_runtime_or_none(
        app_state=app_state,
        engine=engine,
        seed=completion_oracle_seed,
        config=completion_oracle_config,
    )
    vision_gate = await build_vision_gate_or_none(
        app_state=app_state,
        workspace_root=workspace_root,
    )
    logger.info(
        API_APP_STARTUP,
        service="runtime_services",
        mode="agent_engine_built",
        coordinator_wired=coordinator is not None,
        work_pipeline_wired=work_pipeline is not None,
        red_team_wired=red_team_runtime is not None,
        completion_oracle_wired=completion_oracle_runtime is not None,
        vision_gate_wired=vision_gate is not None,
        security_enabled=security.enabled,
        security_enforcement_mode=security.enforcement_mode.value,
    )
    return RuntimeServices(
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
        work_pipeline=work_pipeline,
        red_team_runtime=red_team_runtime,
        completion_oracle_runtime=completion_oracle_runtime,
        completion_oracle_enabled=completion_oracle_config.enabled,
        vision_gate=vision_gate,
    )


# Serialises the whole rebuild + swap so two concurrent reloads (rapid MCP
# installs, or a catalog install racing /setup/complete) cannot interleave
# their per-service swaps and leave AppState pairing a coordinator from one
# build with a worker-execution service from another. Module-level, mirroring
# the setup path's COMPLETE_LOCK; asyncio.Lock binds to the loop on first
# acquire, not at construction.
_RUNTIME_RELOAD_LOCK = asyncio.Lock()


async def reload_runtime_services(
    app_state: AppState, *, trigger: str = "unspecified"
) -> None:
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

    Args:
        app_state: The state to rebuild and swap into.
        trigger: What prompted this rebuild, for the logs only. Dozens of
            watched settings, provider setup and an MCP catalog install all
            land here, so without it a reload line cannot be attributed to
            what caused it.

    Raises:
        Exception: Propagated from ``build_runtime_services`` (or a swap)
            so the caller decides whether a failure is fatal (setup
            reinit) or best-effort (MCP reload).
    """
    from synthorg.client.runtime_builder import (  # noqa: PLC0415
        reload_client_simulation_runtime,
    )
    from synthorg.client.state import (  # noqa: PLC0415
        ClientStateSlice,
        has_simulation_runtime,
    )
    from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
        wire_real_intake_entry,
        wire_real_objective_entry,
        wire_real_task_board_entry,
    )
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )

    async with _RUNTIME_RELOAD_LOCK:
        # Rebuild the client-simulation state from the live settings DB BEFORE
        # the coordinator: the coordinator captures the intake engine at
        # assembly, so the state must reflect the latest intake_strategy /
        # model / review pipeline first. ``reload_client_simulation_runtime``
        # commits the new state into AppState eagerly, so capture the live state
        # first to roll it back if the coordinator rebuild then fails: otherwise
        # the new simulation state would stay live against the still-wired old
        # coordinator, diverging on which intake engine each uses. Only when a
        # simulation runtime was composed at boot (a TaskEngine was present);
        # otherwise there is nothing to refresh.
        sim_present = has_simulation_runtime(app_state)
        previous_sim_state = (
            app_state.slice(ClientStateSlice).simulation_state if sim_present else None
        )
        coordinator_swapped = False
        try:
            if sim_present:
                await reload_client_simulation_runtime(app_state)
            services = await build_runtime_services(
                app_state,
                workspace_root=agent_workspace_root_of(app_state),
            )
            app_state.swap_worker_execution_service(services.worker_execution_service)
            if services.coordinator is not None:
                app_state.swap_coordinator(services.coordinator)
                coordinator_swapped = True
            else:
                # No provider on this rebuild: unwire the stale coordinator so
                # /coordinate goes offline instead of routing through an engine
                # for a provider that is gone.
                app_state.clear_coordinator()
            if services.work_pipeline is not None:
                app_state.swap_work_pipeline(services.work_pipeline)
            else:
                # Clear the stale spine so the entry adapters below resolve it as
                # absent and uninstall themselves (they captured it by
                # reference, so skipping the swap alone leaves them routing
                # through the dead pipeline).
                app_state.clear_work_pipeline()
            # No new coordinator (the documented no-provider success path returns
            # ``coordinator=None`` without raising): the previously wired
            # coordinator, if any, keeps the old intake engine, so revert the
            # eagerly-committed simulation state to stay paired with it BEFORE the
            # intake adapters read it. Mirrors the except-block rollback; only a
            # swapped-in new coordinator (which captured the new intake engine)
            # makes the new simulation state the consistent pairing.
            if (
                sim_present
                and previous_sim_state is not None
                and not coordinator_swapped
            ):
                app_state.wire(ClientStateSlice, simulation_state=previous_sim_state)
            await wire_real_intake_entry(app_state, hot_swap=True)
            await wire_real_objective_entry(app_state, hot_swap=True)
            await wire_real_task_board_entry(app_state, hot_swap=True)
            # Re-attach the completion-oracle gates to the persistent review
            # gate: ``build_runtime_services`` rebuilds the oracle runtime from
            # the live settings, but the gates live on the review-gate service
            # (not the swapped worker service), so a rebuild alone would leave
            # the stale gate attached. This makes the oracle settings
            # hot-reloadable (enable / shadow / min-stakes / reviewer tier).
            attach_completion_oracle_gates(
                app_state,
                enabled=services.completion_oracle_enabled,
                completion_oracle_runtime=services.completion_oracle_runtime,
            )
        except Exception as exc:
            reraise_critical(exc)
            # Roll back the eagerly-committed simulation state while no new
            # coordinator was swapped in (same condition as the success path
            # above): a swapped-in coordinator has already captured the new
            # intake engine, so the new simulation state is then the consistent
            # pairing and must stay.
            if (
                sim_present
                and previous_sim_state is not None
                and not coordinator_swapped
            ):
                app_state.wire(ClientStateSlice, simulation_state=previous_sim_state)
            logger.error(
                WORKERS_RUNTIME_HOT_SWAP_FAILED,
                trigger=trigger,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            WORKERS_RUNTIME_RELOADED,
            trigger=trigger,
            coordinator_swapped=services.coordinator is not None,
            pipeline_swapped=services.work_pipeline is not None,
        )
