# module-kind: orchestrator
"""On-startup wiring for the optional feature engines.

Each ``_wire_*`` helper composes one feature's services into its state
slice once persistence (and, where required, a provider) is connected;
all are best-effort + idempotent (an already-set slice field
short-circuits), so a re-entered lifespan never double-wires and a
missing collaborator leaves its controllers to 503 rather than poisoning
startup. ``wire_features_on_startup`` runs them in dependency order.
"""

from typing import TYPE_CHECKING

from synthorg.api._app_wiring import _wire_steering_service
from synthorg.api.app_builders import build_chief_of_staff_chat
from synthorg.api.lifecycle_helpers.charter_wiring import _wire_charter_engine
from synthorg.api.lifecycle_helpers.conversational_wiring import (
    wire_chief_of_staff_proposer,
    wire_conversational_actor,
    wire_group_chat_service,
)
from synthorg.api.lifecycle_helpers.deliverable_receipt_wiring import (
    _wire_deliverable_receipts,
)
from synthorg.api.lifecycle_helpers.finetune_wiring import (
    _wire_fine_tune_orchestrator,
)
from synthorg.api.lifecycle_helpers.meta_wiring import (
    _wire_analytics_collector,
    _wire_analytics_service,
    _wire_org_inflection_monitor,
    _wire_reports_service,
)
from synthorg.api.lifecycle_helpers.narrative_wiring import wire_run_narrator
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.project_brain.factory import ProjectBrainRuntime
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.research.config import ResearchConfig
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


async def _wire_docs_engine(app_state: AppState) -> None:
    """Wire the living-documentation engine once persistence + workspace exist."""
    from synthorg.docs_engine.state import DocsStateSlice  # noqa: PLC0415
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        WorkspaceStateSlice,
    )
    from synthorg.memory.state import (  # noqa: PLC0415
        MemoryStateSlice,
        memory_backend_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    workspace_service = app_state.slice(WorkspaceStateSlice).project_workspace_service
    if workspace_service is None:
        return
    if app_state.slice(DocsStateSlice).service is not None:
        return
    from synthorg.docs_engine.factory import build_docs_service  # noqa: PLC0415
    from synthorg.docs_engine.tool_factory import DocsToolFactory  # noqa: PLC0415

    if app_state.slice(MemoryStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="docs_engine",
            note="memory backend not wired; docs engine wiring skipped",
        )
        return
    runtime = build_docs_service(
        repo=persistence_of(app_state).project_docs,
        workspace_service=workspace_service,
        git_backend=workspace_service.git_backend,
        memory_backend=memory_backend_of(app_state),
        clock=app_state.clock,
    )
    docs_tool_factory = DocsToolFactory(docs_service=runtime.docs_service)
    app_state.swap_slice(
        DocsStateSlice(
            service=runtime.docs_service,
            memory_facade=runtime.memory_facade,
            tool_factory=docs_tool_factory,
        )
    )
    logger.info(API_APP_STARTUP, service="docs_engine", note="wired")


async def _wire_project_brain(app_state: AppState) -> None:
    """Wire the long-horizon project brain once persistence + workspace exist.

    Best-effort and gated on a connected persistence backend, a project
    workspace, and a memory backend (the brain indexes entries for RAG re-entry
    and commits snapshots through the workspace). The shared
    :class:`ProjectAwareMemoryFacade` already fans out to the brain leg (the docs
    factory builds it with ``brain_enabled=True``); this hook builds the service
    and the per-task tool factory and parks them on the state slice. A missing
    collaborator leaves the brain controllers + MCP handlers to 503 rather than
    poisoning startup.
    """
    from synthorg.engine.workspace.state import WorkspaceStateSlice  # noqa: PLC0415
    from synthorg.memory.state import (  # noqa: PLC0415
        MemoryStateSlice,
        memory_backend_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    workspace_service = app_state.slice(WorkspaceStateSlice).project_workspace_service
    if workspace_service is None:
        return
    if app_state.slice(ProjectBrainStateSlice).service is not None:
        return
    if app_state.slice(MemoryStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="project_brain",
            note="memory backend not wired; project brain wiring skipped",
        )
        return
    from synthorg.project_brain.factory import (  # noqa: PLC0415
        build_project_brain_service,
    )

    runtime = build_project_brain_service(
        repo=persistence_of(app_state).project_brain,
        workspace_service=workspace_service,
        git_backend=workspace_service.git_backend,
        memory_backend=memory_backend_of(app_state),
        clock=app_state.clock,
    )
    app_state.swap_slice(
        ProjectBrainStateSlice(
            service=runtime.brain_service,
            tool_factory=runtime.tool_factory,
        )
    )
    logger.info(API_APP_STARTUP, service="project_brain", note="wired")
    await _replay_project_brain_index(app_state, runtime)


async def _replay_project_brain_index(
    app_state: AppState,
    runtime: ProjectBrainRuntime,
) -> None:
    """Best-effort boot replay of the brain RAG index gap.

    Re-indexes brain entries that were persisted but whose index write failed
    (so they are invisible to transparent re-entry retrieval). Never poisons
    startup: a failure is logged and swallowed.
    """
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    try:
        projects = await persistence_of(app_state).projects.list_items(limit=10_000)
        project_ids = tuple(str(project.id) for project in projects)
        if not project_ids:
            return
        reindexed = await runtime.replay_unindexed(project_ids=project_ids)
        logger.info(
            API_APP_STARTUP,
            service="project_brain",
            note="index replay complete",
            reindexed=reindexed,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="project_brain",
            note="index replay skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_knowledge_engine(app_state: AppState) -> None:
    """Wire the knowledge + provenance substrate once persistence + memory exist."""
    from synthorg.knowledge.state import KnowledgeStateSlice  # noqa: PLC0415
    from synthorg.memory.state import (  # noqa: PLC0415
        MemoryStateSlice,
        memory_backend_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    if app_state.slice(KnowledgeStateSlice).service is not None:
        return
    if app_state.slice(MemoryStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="memory backend not wired; knowledge engine wiring skipped",
        )
        return
    from synthorg.knowledge.config import KnowledgeConfig  # noqa: PLC0415
    from synthorg.knowledge.factory import build_knowledge_service  # noqa: PLC0415
    from synthorg.knowledge.tool_factory import (  # noqa: PLC0415
        build_knowledge_tool_factory,
    )

    service = build_knowledge_service(
        memory_backend=memory_backend_of(app_state),
        persistence=persistence_of(app_state),
        config=KnowledgeConfig(enabled=True),
        clock=app_state.clock,
    )
    tool_factory = build_knowledge_tool_factory(service=service)
    app_state.swap_slice(
        KnowledgeStateSlice(service=service, tool_factory=tool_factory)
    )
    logger.info(API_APP_STARTUP, service="knowledge_engine", note="wired")


async def _wire_research_engine(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
) -> None:
    """Wire the research subsystem behind research.enabled + research.model."""
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )
    from synthorg.research.state import ResearchStateSlice  # noqa: PLC0415
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        settings_service_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    if app_state.slice(ResearchStateSlice).service is not None:
        return
    if (
        app_state.slice(SettingsStateSlice).settings_service is None
        or provider_registry is None
    ):
        return
    runtime_settings = settings_service_of(app_state)
    try:
        await _build_and_wire_research(
            app_state,
            provider_registry=provider_registry,
            runtime_settings=runtime_settings,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="research_engine",
            note="research engine wiring unavailable; skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _build_research_config(runtime_settings: SettingsService) -> ResearchConfig:
    """Assemble the ``ResearchConfig`` from the research settings namespace.

    Returns:
        The strategy-discriminator + tuning ``ResearchConfig``.
    """
    from synthorg.research.config import ResearchConfig  # noqa: PLC0415

    return ResearchConfig(
        enabled=True,
        query_planner=(
            await runtime_settings.get("research", "query_planner")
        ).value.strip(),  # type: ignore[arg-type]
        credibility_triage=(
            await runtime_settings.get("research", "credibility_triage")
        ).value.strip(),  # type: ignore[arg-type]
        deduplicator=(
            await runtime_settings.get("research", "deduplicator")
        ).value.strip(),  # type: ignore[arg-type]
        synthesizer=(
            await runtime_settings.get("research", "synthesizer")
        ).value.strip(),  # type: ignore[arg-type]
        triage_batch_size=int(
            (await runtime_settings.get("research", "triage_batch_size")).value
        ),
        hybrid_prefilter_factor=float(
            (await runtime_settings.get("research", "hybrid_prefilter_factor")).value
        ),
        dedup_similarity_threshold=float(
            (await runtime_settings.get("research", "dedup_similarity_threshold")).value
        ),
        per_query_limit=int(
            (await runtime_settings.get("research", "per_query_limit")).value
        ),
    )


async def _build_and_wire_research(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry,
    runtime_settings: SettingsService,
) -> None:
    """Build the research service from config and swap it onto the slice.

    No-op (logs + returns) when research is disabled, no model is set, or no
    provider is configured.
    """
    from synthorg.budget.state import cost_tracker_of  # noqa: PLC0415
    from synthorg.knowledge.state import KnowledgeStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415
    from synthorg.research.factory import build_research_service  # noqa: PLC0415
    from synthorg.research.state import ResearchStateSlice  # noqa: PLC0415
    from synthorg.research.tool_factory import (  # noqa: PLC0415
        build_research_tool_factory,
    )

    enabled = (
        await runtime_settings.get("research", "enabled")
    ).value.strip().lower() == "true"
    model = (await runtime_settings.get("research", "model")).value.strip()
    if not enabled or not model:
        logger.info(
            API_APP_STARTUP,
            service="research_engine",
            note="research disabled or model unset; wiring skipped",
        )
        return
    provider_names = provider_registry.list_providers()
    if not provider_names:
        return
    provider_name = (await runtime_settings.get("research", "provider")).value.strip()
    provider = (
        provider_registry.get(provider_name)
        if provider_name and provider_name in provider_registry
        else provider_registry.get(provider_names[0])
    )
    service = build_research_service(
        runs_repo=persistence_of(app_state).research_runs,
        provider=provider,
        model=model,
        config=await _build_research_config(runtime_settings),
        knowledge_service=app_state.slice(KnowledgeStateSlice).service,
        clock=app_state.clock,
        cost_tracker=cost_tracker_of(app_state),
    )
    tool_factory = build_research_tool_factory(service=service, clock=app_state.clock)
    app_state.swap_slice(ResearchStateSlice(service=service, tool_factory=tool_factory))
    logger.info(API_APP_STARTUP, service="research_engine", note="wired")


async def _wire_signals_service(
    app_state: AppState,
    *,
    effective_approval_store: ApprovalStoreProtocol,
) -> None:
    """Wire the org-signals facade once persistence + a tracker exist.

    Best-effort + idempotent. Gated on a connected persistence backend
    and a performance tracker (the one hard aggregator dependency); the
    scaling service and the error / evolution / telemetry stores are
    optional and degrade to empty per-domain summaries when absent, so
    the signals MCP handlers and ``/meta/chat`` signal reads come online
    rather than 503-ing.
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )

    if app_state.slice(MetaStateSlice).signals_service is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    performance_tracker = app_state.slice(HrStateSlice).performance_tracker
    if performance_tracker is None:
        logger.info(
            API_APP_STARTUP,
            service="signals",
            note="performance tracker absent; signals wiring skipped",
        )
        return
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.meta.signals.factory import build_signals_service  # noqa: PLC0415

    registry = app_state.slice(HrStateSlice).agent_registry
    agent_ids_provider = registry.active_agent_ids if registry is not None else tuple
    # The evolution-outcome store is intentionally left unwired: its only
    # producer is the self-improvement cycle (SelfImprovementService), which
    # is out of scope here, so wiring a read-only store with no writer would
    # be a ghost. The aggregator degrades to an empty evolution summary until
    # that producer ships. The error-taxonomy store IS wired -- it has a live
    # producer (the classification sinks).
    try:
        signals_service = build_signals_service(
            performance_tracker=performance_tracker,
            agent_ids_provider=agent_ids_provider,
            approval_store=effective_approval_store,
            scaling_service=app_state.slice(HrStateSlice).scaling_service,
            error_store=app_state.slice(EngineStateSlice).error_taxonomy_store,
        )
        app_state.wire(MetaStateSlice, signals_service=signals_service)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="signals",
            note="signals wiring failed; MCP handlers and /meta/chat will 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="signals", note="wired")


async def _wire_chief_of_staff_chat(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTracker | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the Chief of Staff chat backend behind chief_of_staff.chat_enabled."""
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).chief_of_staff_chat is not None:
        return
    if provider_registry is None:
        return

    chat_backend = build_chief_of_staff_chat(
        si_config.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    if chat_backend is not None:
        app_state.wire(MetaStateSlice, chief_of_staff_chat=chat_backend)
        logger.info(API_APP_STARTUP, service="chief_of_staff_chat", note="wired")


async def wire_features_on_startup(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTracker | None,
    effective_approval_store: ApprovalStoreProtocol,
) -> None:
    """Run every optional feature-engine wire in dependency order."""
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    # Load the self-improvement config ONCE for the whole feature-wiring
    # pass and thread it into every sibling helper; the slice is rewritten
    # at runtime by ``/setup``, so this boot snapshot is not cached there.
    si_config = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    await _wire_docs_engine(app_state)
    await _wire_project_brain(app_state)
    await _wire_steering_service(app_state, provider_registry=provider_registry)
    await _wire_knowledge_engine(app_state)
    await _wire_deliverable_receipts(app_state)
    await _wire_fine_tune_orchestrator(app_state)
    await _wire_research_engine(app_state, provider_registry=provider_registry)
    await _wire_charter_engine(
        app_state,
        provider_registry=provider_registry,
        persistence=persistence,
        cost_tracker=cost_tracker,
        si_config=si_config,
    )
    await _wire_meta_features(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        effective_approval_store=effective_approval_store,
        si_config=si_config,
    )
    await wire_run_narrator(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        si_config=si_config,
    )
    await wire_chief_of_staff_proposer(
        app_state,
        provider_registry=provider_registry,
        persistence=persistence,
        cost_tracker=cost_tracker,
        effective_approval_store=effective_approval_store,
        si_config=si_config,
    )
    await wire_group_chat_service(
        app_state,
        provider_registry=provider_registry,
        persistence=persistence,
        cost_tracker=cost_tracker,
        si_config=si_config,
    )
    await wire_conversational_actor(app_state, si_config=si_config)


async def _wire_meta_features(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTracker | None,
    effective_approval_store: ApprovalStoreProtocol,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the signals facade, its read-views, and chief-of-staff chat.

    Ordered: signals first (the analytics / reports / inflection views layer
    on top of it), then the cross-deployment collector role and the
    chief-of-staff chat backend.
    """
    await _wire_signals_service(
        app_state,
        effective_approval_store=effective_approval_store,
    )
    await _wire_analytics_service(app_state)
    await _wire_reports_service(app_state)
    await _wire_org_inflection_monitor(app_state, si_config=si_config)
    await _wire_analytics_collector(si_config=si_config)
    await _wire_chief_of_staff_chat(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        si_config=si_config,
    )
