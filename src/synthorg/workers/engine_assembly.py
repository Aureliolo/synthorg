# module-kind: orchestrator
"""The ONE place an ``AgentEngine`` is constructed.

Public because it has to be: the runtime-services builder calls it at
boot and the recording harness calls it per session, and a second
assembly is how a harness came to build an engine with 8 of the 51
collaborators a deployment supplies, for eight recordings, with nothing
at any layer able to tell.

Owns the engine-side construction steps: the sandbox + tool registry, the
optional external-access runtime, the compaction callback, and
:func:`build_agent_engine`, which reads every collaborator off the live
``AppState`` and declares each one into
:class:`~synthorg.engine.dependencies.EngineDependencies`.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg._core.features import require_service
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.state import BudgetStateSlice, budget_enforcer_of
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.agent_state_recording import AgentStateRepositoryProvider
from synthorg.engine.artifacts.baseline_scope import RunBaselineProbe
from synthorg.engine.background_job_watch import create_background_job_watcher
from synthorg.engine.checkpoint.wiring import CheckpointWiring
from synthorg.engine.dependencies import (
    EngineBehaviour,
    EngineBudget,
    EngineCore,
    EngineDependencies,
    EngineGovernance,
    EngineLoopControls,
    EngineMemory,
    EngineObservability,
    EngineOrg,
    EngineRecovery,
    EngineRouting,
    EngineTooling,
)
from synthorg.engine.flight_recording import FlightRecorderSink
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.recovery import RecoveryStrategy
from synthorg.engine.recovery_factory import build_recovery_strategy
from synthorg.engine.stagnation import create_stagnation_detector
from synthorg.engine.state import EngineStateSlice, task_engine_of
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import (
    get_logger,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.evolution import EVOLUTION_PROPOSER_MODEL_UNSET
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.memory_protocol import OrgFactRepository
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.persistence.state import (
    PersistenceStateSlice,
    project_repository_of,
)
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.providers.model_binding import resolve_bound_completion
from synthorg.security.audit import AuditLog
from synthorg.security.config import SecurityConfig
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
from synthorg.tools.registry import ToolRegistry
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
from synthorg.workers._background_job_wiring import (
    background_job_registry_or_none,
)
from synthorg.workers._capability_policy_wiring import build_capability_policy
from synthorg.workers._classification_assembly import build_classification
from synthorg.workers._memory_assembly import (
    MemoryInjectionResolver,
    resolved_procedural_config,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.evolution.service import EvolutionService
    from synthorg.engine.quality.classifier import StepQualityClassifier
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.tools.external_api._runtime import ExternalApiRuntime

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"
_TOOLS_NS: str = "tools"
_GIT_LOG_MAX_COUNT_KEY: str = "git_log_max_count"
_CODE_RUNNER_OUTPUT_TAIL_KEY: str = "code_runner_output_tail_limit"
_EXTERNAL_API_NS: str = SettingNamespace.EXTERNAL_API.value


async def _build_auto_review_pipeline_or_none(
    app_state: AppState,
) -> ReviewPipeline | None:
    """Build the auto-review pipeline when the operator has opted in.

    Returns ``None`` unless ``engine.auto_review_on_completion`` is set, so
    the agent runtime is threaded a pipeline only when auto-review is enabled;
    absent it, ``apply_post_execution_transitions`` leaves completed work in
    IN_REVIEW for a human.

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


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineAssemblyInputs:
    """What the CALLER of :func:`build_agent_engine` owns.

    Everything else is read from the live ``AppState``, which is what makes
    this the single construction path: an engine built for a recording and
    an engine built at boot differ only in these fields, and each is
    something the caller genuinely knows and the application state does
    not.

    Every field is required, for the same reason every field of
    :class:`~synthorg.engine.dependencies.EngineDependencies` is: an
    omitted keyword is a decision nobody made.

    Attributes:
        provider: The completion driver this engine dispatches through.
        provider_registry: Where an agent's own bound pair is resolved.
        tool_registry: The tools an agent starts from, scoped to whatever
            workspace this caller runs in. ``None`` builds no tool invoker
            at all, so the agent answers in prose.
        run_probe: Captures how that workspace looked before the run. The
            engine holds no workspace root, so the layout knowledge stays
            with the caller and the engine receives only the question it
            can ask: did this project produce what its task declared.
        coordination_metrics_collector: Shared with the coordinator when
            one exists, so multi-agent metrics compare against the same
            single-agent baselines.
        external_api_runtime: The governed external-access runtime.
        connection_tool_runtimes: Per-family connection tool runtimes.
        flight_recorder_sink: Durable per-turn frames.
        step_classifier: Per-step quality scoring.
        classification_detector_timeout_seconds: How long the failure
            detectors get.
    """

    provider: CompletionProvider
    provider_registry: ProviderRegistry
    tool_registry: ToolRegistry | None
    run_probe: RunBaselineProbe
    coordination_metrics_collector: CoordinationMetricsCollector | None
    external_api_runtime: ExternalApiRuntime | None
    connection_tool_runtimes: ConnectionToolRuntimes | None
    flight_recorder_sink: FlightRecorderSink | None
    step_classifier: StepQualityClassifier | None
    classification_detector_timeout_seconds: float | None


async def build_agent_engine(
    app_state: AppState,
    inputs: EngineAssemblyInputs,
) -> AgentEngine:
    """Assemble an ``AgentEngine`` from live application state.

    The ONE place an engine is constructed. A boot instance is shared by
    the worker execution service and the coordinator's parallel executor,
    so both observe the same interrupt store, event stream hub and clock
    seam; a recording harness calls the same function against its own
    application state so what it measures is what a deployment runs.

    A second assembly is how a harness came to build an engine with 8 of
    the 51 collaborators a deployment supplies, for eight recordings,
    with nothing at any layer able to tell.

    Args:
        app_state: The live application state every other collaborator is
            read from.
        inputs: What this caller owns and the state cannot answer.

    Returns:
        The wired ``AgentEngine``.
    """
    error_taxonomy_config, classification_sinks = build_classification(
        app_state,
        detector_timeout_seconds=inputs.classification_detector_timeout_seconds,
    )
    provider = inputs.provider
    resolver = config_resolver_of(app_state)
    budget = app_state.slice(BudgetStateSlice)
    approval = app_state.slice(ApprovalStateSlice)
    communication = app_state.slice(CommunicationStateSlice)
    security = app_state.slice(SecurityStateSlice)
    return AgentEngine(
        EngineDependencies(
            core=EngineCore(
                provider=provider,
                clock=app_state.clock,
                config_resolver=resolver,
                tool_registry=inputs.tool_registry,
                # The engine builds its own loop, wired with every in-flight
                # control it holds; supplying one here would take ownership
                # of that wiring away from the one place that has it all.
                execution_loop=None,
                shutdown_checker=None,
            ),
            routing=EngineRouting(
                provider_registry=inputs.provider_registry,
                provider_configs=None,
                model_resolver=None,
            ),
            budget=EngineBudget(
                cost_tracker=budget.cost_tracker,
                budget_enforcer=budget_enforcer_of(app_state),
                cost_forecast_repo=budget.cost_forecast_repo,
                coordination_metrics_collector=(inputs.coordination_metrics_collector),
            ),
            governance=EngineGovernance(
                policy_engine=security.policy_engine,
                security_config=app_state.config.security,
                # Live security config the per-request interceptor reads, so
                # an operator toggle of the security.* flags applies without
                # a restart.
                security_config_provider=(
                    lambda: app_state.security_runtime_config.current
                ),
                # Named here rather than defaulted inside the engine: an
                # engine auditing into a fresh in-memory log is a decision,
                # and this is where the deployment gets to make it.
                audit_log=security.audit_log or AuditLog(),
                approval_store=require_service(approval.store, "Approval Store"),
                approval_gate=approval.gate,
                parked_context_repo=_parked_context_repo_or_none(app_state),
                approval_interrupt_timeout_seconds=None,
                review_gate=approval.review_gate,
                review_pipeline=await _build_auto_review_pipeline_or_none(app_state),
            ),
            loop_controls=EngineLoopControls(
                stagnation_detector=create_stagnation_detector(
                    app_state.config.stagnation
                ),
                compaction_callback=_build_compaction_callback(app_state, provider),
                step_classifier=inputs.step_classifier,
                steering_inbox=boot_steering_inbox(app_state),
                background_job_watcher=create_background_job_watcher(
                    app_state.config.background_job_staleness,
                    registry=background_job_registry_or_none(app_state),
                ),
            ),
            memory=EngineMemory(
                memory_backend=app_state.slice(MemoryStateSlice).backend,
                # A resolver, not a strategy: memory can be wired after the
                # engine is built (an embedder that was unreachable at boot
                # and is reachable now), and a captured strategy would leave
                # those agents with no recall until the process restarted.
                memory_injection_strategy_provider=MemoryInjectionResolver(
                    app_state,
                    provider=provider,
                    cost_tracker=budget.cost_tracker,
                ),
                ontology_injection_strategy=None,
                # The write side: without this an agent recalls but never
                # learns, so a second run of the same objective starts from
                # nothing. The engine re-reads the capture switch per task,
                # so this is the boot fallback rather than the live value.
                procedural_memory_config=await resolved_procedural_config(app_state),
                capture_strategy=None,
                distillation_capture_enabled=await resolver.get_bool(
                    "memory", "distillation_capture_enabled"
                ),
            ),
            org=EngineOrg(
                agent_registry=agent_registry_of(app_state),
                # The SAME instance selection judges against, so a task
                # assigned by the ladder always clears here. This is the last
                # line for a pair selection never saw: a hand-assigned task,
                # or a reassignment.
                capability=await build_capability_policy(app_state),
                task_engine=task_engine_of(app_state),
                # Membership and per-project budget are validated against
                # this repo before a run starts; a work task refuses to run
                # without it, so the composition root hands it over rather
                # than leaving the engine to discover it is missing at
                # dispatch.
                project_repo=project_repository_of(app_state),
                # Bound after construction: the coordinator is built FROM
                # this engine, so naming it here would be a cycle.
                coordinator=None,
                evolution_service=await _build_evolution_service_or_none(app_state),
                # Read from the LIVE security config, not the boot snapshot:
                # the mode is an operator setting, and a rebuild triggered by
                # that setting has to see the value it was triggered by.
                mcp_self_consumer=build_mcp_self_consumer(
                    _live_security(app_state).mcp_self_consumer,
                    app_state,
                ),
            ),
            tooling=EngineTooling(
                external_api_runtime=inputs.external_api_runtime,
                connection_tool_runtimes=(
                    inputs.connection_tool_runtimes or ConnectionToolRuntimes()
                ),
                tool_invocation_tracker=None,
                brain_tool_factory_provider=boot_brain_tool_factory_provider(app_state),
                knowledge_tool_factory_provider=(
                    boot_knowledge_tool_factory_provider(app_state)
                ),
                docs_tool_factory_provider=boot_docs_tool_factory_provider(app_state),
                research_tool_factory_provider=(
                    boot_research_tool_factory_provider(app_state)
                ),
                structure_map_tool_factory_provider=(
                    boot_structure_map_tool_factory_provider(app_state)
                ),
            ),
            observability=EngineObservability(
                event_stream_hub=communication.event_stream_hub,
                event_reader=None,
                interrupt_store=communication.interrupt_store,
                flight_recorder_sink=inputs.flight_recorder_sink,
                # A provider, not the repository: a run can start before
                # persistence is connected, and a captured ``None`` would
                # leave that agent absent from the live view for the life of
                # the process.
                agent_state_repository_provider=agent_state_repository_provider(
                    app_state
                ),
                classification_sinks=classification_sinks,
                error_taxonomy_config=error_taxonomy_config,
                agent_middleware_chain=await build_agent_middleware_chain_or_none(
                    app_state,
                    error_taxonomy_config=error_taxonomy_config,
                ),
            ),
            recovery=EngineRecovery(
                recovery_strategy=_build_recovery_strategy(app_state),
                run_probe=inputs.run_probe,
                # The engine's own checkpointing is the recovery strategy's
                # concern here: the strategy holds the same wiring, and a
                # second copy on the engine is a second answer to whether
                # this run is checkpointed.
                checkpointing=_checkpoint_wiring_or_none(app_state),
            ),
            behaviour=EngineBehaviour(
                clarification_enabled=await resolver.get_bool(
                    "engine", "clarification_enabled"
                ),
                scoping_enabled=await resolver.get_bool("engine", "scoping_enabled"),
            ),
        )
    )


def _checkpoint_wiring_or_none(app_state: AppState) -> CheckpointWiring | None:
    """Resolve the checkpoint repositories, or ``None`` before persistence.

    Returns:
        The wiring, or ``None`` when persistence is unconnected, in which
        case a run does not survive its process.
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None or not getattr(backend, "is_connected", False):
        return None
    return CheckpointWiring(
        checkpoint_repo=backend.checkpoints,
        heartbeat_repo=backend.heartbeats,
        config=app_state.config.recovery.checkpoint,
    )


def _build_recovery_strategy(app_state: AppState) -> RecoveryStrategy:
    """Wire the configured crash-recovery strategy with its persistence deps.

    The checkpoint strategy needs both repositories and the
    operator-tunable ``CheckpointConfig`` from ``config.recovery.checkpoint``,
    supplied together so an operator selecting ``recovery.strategy =
    checkpoint`` gets a working, correctly-tuned strategy instead of a
    boot-time ``RecoveryConfigError``. The fail-reassign default ignores it.

    Returns:
        The recovery strategy for this ``AgentEngine``.
    """
    return build_recovery_strategy(
        app_state.config.recovery,
        checkpointing=_checkpoint_wiring_or_none(app_state),
    )
