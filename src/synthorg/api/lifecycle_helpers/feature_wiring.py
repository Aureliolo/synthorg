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
    wire_conversational_plan_dispatcher,
    wire_group_chat_service,
)
from synthorg.api.lifecycle_helpers.deliverable_receipt_wiring import (
    _wire_deliverable_receipts,
)
from synthorg.api.lifecycle_helpers.finetune_wiring import (
    _wire_fine_tune_orchestrator,
)
from synthorg.api.lifecycle_helpers.kanban_wiring import wire_kanban_board
from synthorg.api.lifecycle_helpers.knowledge_wiring import wire_knowledge_engine
from synthorg.api.lifecycle_helpers.meta_apply_wiring import wire_meta_apply
from synthorg.api.lifecycle_helpers.meta_wiring import (
    _wire_ab_test_repo,
    _wire_alert_repo,
    _wire_analytics_collector,
    _wire_analytics_service,
    _wire_experiment_service,
    _wire_org_inflection_monitor,
    _wire_reports_service,
)
from synthorg.api.lifecycle_helpers.narrative_wiring import wire_run_narrator
from synthorg.api.lifecycle_helpers.org_memory_wiring import (
    wire_org_memory_backend,
)
from synthorg.api.lifecycle_helpers.organization_wiring import (
    wire_organization_read_services,
)
from synthorg.api.lifecycle_helpers.plan_review_wiring import (
    wire_plan_review_gate,
    wire_plan_review_services,
)
from synthorg.api.lifecycle_helpers.refinement_wiring import wire_refinement_router
from synthorg.api.lifecycle_helpers.sprint_wiring import wire_sprint_service
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
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


async def _wire_custom_rules_service(app_state: AppState) -> None:
    """Wire the custom-rules service once persistence is connected.

    Best-effort + idempotent. The service is a thin facade over the
    ``custom_rules`` repository; wiring it up front keeps the meta MCP
    ``list_rules`` handler off ``persistence.*`` (it resolves the wired
    service and 503s when absent rather than constructing per call).
    """
    from synthorg.meta.rules.service import CustomRulesService  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(MetaStateSlice).custom_rules_service is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    service = CustomRulesService(repo=persistence_of(app_state).custom_rules)
    app_state.wire(MetaStateSlice, custom_rules_service=service)
    logger.info(API_APP_STARTUP, service="custom_rules", note="wired")


async def _wire_budget_versions_service(app_state: AppState) -> None:
    """Wire the budget-config versions service once persistence is connected.

    Best-effort + idempotent. The service reads the append-only
    budget-config version history; wiring it up front keeps the budget
    MCP version handlers off ``persistence.*`` (they resolve the wired
    service and 503 when absent rather than constructing per call).
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.budget.version_service import (  # noqa: PLC0415
        BudgetConfigVersionsService,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(BudgetStateSlice).budget_versions_service is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    service = BudgetConfigVersionsService(
        version_repo=persistence_of(app_state).budget_config_versions,
    )
    app_state.wire(BudgetStateSlice, budget_versions_service=service)
    logger.info(API_APP_STARTUP, service="budget_versions", note="wired")


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

    # One batched namespace read instead of eight sequential get() round-trips.
    values = {
        entry.definition.key: entry.value
        for entry in await runtime_settings.get_namespace("research")
    }
    return ResearchConfig(
        enabled=True,
        query_planner=values["query_planner"].strip(),  # type: ignore[arg-type]
        credibility_triage=values["credibility_triage"].strip(),  # type: ignore[arg-type]
        deduplicator=values["deduplicator"].strip(),  # type: ignore[arg-type]
        synthesizer=values["synthesizer"].strip(),  # type: ignore[arg-type]
        triage_batch_size=int(values["triage_batch_size"]),
        hybrid_prefilter_factor=float(values["hybrid_prefilter_factor"]),
        dedup_similarity_threshold=float(values["dedup_similarity_threshold"]),
        per_query_limit=int(values["per_query_limit"]),
    )


async def _build_and_wire_research(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry,
    runtime_settings: SettingsService,
) -> None:
    """Build the research service from config and swap it onto the slice.

    Ghost-wired: the service is built whenever a model + provider exist,
    regardless of ``research.enabled``. The master switch is enforced live
    per request at the research MCP handlers (``_require_enabled_service``),
    so toggling ``research.enabled`` takes effect with no restart. No-op
    (logs + returns) only when no model is set or no provider is configured.
    """
    from synthorg.budget.state import cost_tracker_of  # noqa: PLC0415
    from synthorg.knowledge.state import KnowledgeStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415
    from synthorg.research.factory import build_research_service  # noqa: PLC0415
    from synthorg.research.state import ResearchStateSlice  # noqa: PLC0415
    from synthorg.research.tool_factory import (  # noqa: PLC0415
        build_research_tool_factory,
    )
    from synthorg.settings.model_ref import parse_model_ref  # noqa: PLC0415

    # ``research.model`` is a model-assignment setting storing a ``ModelRef``:
    # the provider travels with the model (the picker writes both). A blank
    # ref provider falls back to the explicit default system provider, never
    # a first-registered pick.
    ref = parse_model_ref((await runtime_settings.get("research", "model")).value)
    model = ref.model_id.strip()
    if not model:
        logger.info(
            API_APP_STARTUP,
            service="research_engine",
            note="research model unset; wiring skipped",
        )
        return
    provider_name = ref.provider.strip()
    if provider_name and provider_name not in provider_registry:
        logger.warning(
            API_APP_STARTUP,
            service="research_engine",
            note="configured research provider not registered; wiring skipped",
            provider_name=provider_name,
        )
        return
    provider = (
        provider_registry.get(provider_name)
        if provider_name
        else provider_registry.default_provider()
    )
    if provider is None:
        logger.warning(
            API_APP_STARTUP,
            service="research_engine",
            note=(
                "no default system provider resolvable for research; wiring "
                "skipped until providers.default_provider is set"
            ),
        )
        return
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
    from datetime import datetime  # noqa: PLC0415

    from synthorg.budget.cost_record import CostRecord  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.budget.tracker_protocol import (  # noqa: PLC0415
        collect_all_records,
    )
    from synthorg.coordination.state import (  # noqa: PLC0415
        CoordinationStateSlice,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.meta.signals.budget import CostRecordProvider  # noqa: PLC0415
    from synthorg.meta.signals.factory import build_signals_service  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    registry = app_state.slice(HrStateSlice).agent_registry
    agent_ids_provider = registry.active_agent_ids if registry is not None else tuple
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    cost_record_provider: CostRecordProvider | None = None
    budget_total_monthly = 0.0
    if cost_tracker is not None:
        tracker = cost_tracker

        async def _provider(
            since: datetime,
            until: datetime,
        ) -> tuple[CostRecord, ...]:
            return await collect_all_records(tracker, start=since, end=until)

        cost_record_provider = _provider
        try:
            budget_cfg = await config_resolver_of(app_state).get_budget_config()
            budget_total_monthly = budget_cfg.total_monthly
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                service="signals",
                note="budget config unavailable; budget forecast uses 0 ceiling",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    # The evolution-outcome store is built earlier (``wire_evolution_outcomes``)
    # when persistence is available; the engine evolution loop writes through
    # it as its durable outcome sink. The aggregator shares that same store so
    # the evolution signal domain reflects real, restart-surviving outcomes.
    # When persistence is absent the store is ``None`` and the aggregator
    # degrades to an empty evolution summary. The error-taxonomy store IS wired
    # too -- it has a live producer (the classification sinks).
    from synthorg.meta.state import evolution_outcome_store_of  # noqa: PLC0415

    try:
        signals_service = build_signals_service(
            performance_tracker=performance_tracker,
            agent_ids_provider=agent_ids_provider,
            approval_store=effective_approval_store,
            scaling_service=app_state.slice(HrStateSlice).scaling_service,
            error_store=app_state.slice(EngineStateSlice).error_taxonomy_store,
            evolution_store=evolution_outcome_store_of(app_state),
            budget_total_monthly=budget_total_monthly,
            cost_record_provider=cost_record_provider,
            coordination_metrics_store=app_state.slice(
                CoordinationStateSlice
            ).metrics_store,
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
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Ghost-wire the Chief of Staff chat backend whenever a provider exists.

    Enablement is gated live per request on ``POST /meta/chat`` via
    ``explain_chat_enabled``, not at build time, so the toggle takes
    effect without a restart.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).chief_of_staff_chat is not None:
        return
    if provider_registry is None:
        return
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    chat_backend = build_chief_of_staff_chat(
        si_config.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    if chat_backend is not None:
        app_state.wire(MetaStateSlice, chief_of_staff_chat=chat_backend)
        logger.info(API_APP_STARTUP, service="chief_of_staff_chat", note="wired")


async def wire_features_on_startup(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTrackerProtocol | None,
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
    await wire_org_memory_backend(app_state)
    await wire_knowledge_engine(app_state, provider_registry=provider_registry)
    await _wire_custom_rules_service(app_state)
    await _wire_budget_versions_service(app_state)
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
    # After meta wiring: the settings-composed config resolver + org-mutation
    # service exist, so the organization read facades (company + role version)
    # can project the durable org surface.
    await wire_organization_read_services(app_state, persistence)
    await wire_run_narrator(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        si_config=si_config,
    )
    try:
        await wire_chief_of_staff_proposer(
            app_state,
            provider_registry=provider_registry,
            persistence=persistence,
            cost_tracker=cost_tracker,
            effective_approval_store=effective_approval_store,
            si_config=si_config,
        )
    except ServiceUnavailableError as exc:
        # A propose/invite misconfiguration (e.g. enabled over a persistent
        # SQLite ApprovalStore) makes the guard raise. Degrade to an unwired
        # proposer (the controller 503s) rather than failing the whole ASGI
        # startup and taking every other feature down with it. Any OTHER
        # exception is a genuine wiring fault and must fail the boot rather
        # than silently leaving the proposer unwired.
        logger.warning(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="proposer wiring blocked; degrading to unwired",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    # After the proposer: the refinement router wraps it and attaches to
    # the work pipeline so team-bound work with no definition of done is
    # refined rather than blocked by the coordinator's clarification gate.
    await wire_refinement_router(app_state)
    # Opt-in human plan-approval gate: when enabled, splittable team work is
    # parked for approval before it builds. No-op unless the setting is on.
    await wire_plan_review_gate(app_state)
    # Plan-review provider-backed services: the stakeholder review panel (reviews
    # a gated plan before the human sees it) and the conversational reply service
    # (answers an operator's plan-item comment inline). Both no-op without a
    # provider; gated live per request/comment.
    await wire_plan_review_services(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    # Attach the plan dispatcher to the proposer now the pipeline is up, so a
    # conversational work brief drafts one plan into Plan Review.
    await wire_conversational_plan_dispatcher(app_state)
    # Sprint service: runs real sprints for agile_kanban orgs. Wired before
    # the board so the board's advisory sprint gate has its dependency.
    await wire_sprint_service(app_state)
    # Kanban board service: projects tasks onto the org's board and drives
    # column moves. Wired whenever the task engine + persistence exist.
    await wire_kanban_board(app_state)
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
    cost_tracker: CostTrackerProtocol | None,
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
    await _wire_experiment_service(app_state)
    await _wire_ab_test_repo(app_state)
    await _wire_alert_repo(app_state)
    await wire_meta_apply(app_state)
    await _wire_org_inflection_monitor(app_state, si_config=si_config)
    await _wire_analytics_collector(si_config=si_config)
    await _wire_chief_of_staff_chat(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        si_config=si_config,
    )
