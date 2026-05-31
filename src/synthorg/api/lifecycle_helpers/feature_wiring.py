# module-kind: orchestrator
"""On-startup wiring for the optional feature engines.

Extracted from :mod:`synthorg.api.app` so the application factory stays
focused on construction + the god-module gate keeps net-shrinking. Each
``_wire_*`` helper composes one feature's services into its state slice
once persistence (and, where required, a provider) is connected; all are
best-effort + idempotent (a slice field already set short-circuits) so a
re-entered lifespan does not double-wire and a missing collaborator
leaves the feature's controllers to 503 rather than poisoning startup.

``wire_features_on_startup`` runs them in dependency order (docs,
knowledge, research, charter, chat, proposer): research reads the
knowledge service, and the chief-of-staff proposer expects the chat
backend's conventions.
"""

from typing import TYPE_CHECKING

from synthorg.api.app_builders import (
    build_chief_of_staff_chat,
    build_chief_of_staff_proposer,
)
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.conversational_wiring import (
    wire_group_chat_service,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.charter import CHARTER_SUBSTRATE_UNAVAILABLE

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.tracker import CostTracker
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.project_brain.factory import ProjectBrainRuntime
    from synthorg.providers.registry import ProviderRegistry

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
        project_ids = tuple(project.id for project in projects)
        if not project_ids:
            return
        reindexed = await runtime.replay_unindexed(project_ids=project_ids)
        logger.info(
            API_APP_STARTUP,
            service="project_brain",
            note="index replay complete",
            reindexed=reindexed,
        )
    except Exception as exc:
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
    from synthorg.knowledge.state import KnowledgeStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
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
        from synthorg.research.config import ResearchConfig  # noqa: PLC0415
        from synthorg.research.factory import build_research_service  # noqa: PLC0415
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
        provider_name = (
            await runtime_settings.get("research", "provider")
        ).value.strip()
        provider = (
            provider_registry.get(provider_name)
            if provider_name and provider_name in provider_registry
            else provider_registry.get(provider_names[0])
        )
        config = ResearchConfig(
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
        )
        service = build_research_service(
            runs_repo=persistence_of(app_state).research_runs,
            provider=provider,
            model=model,
            config=config,
            knowledge_service=app_state.slice(KnowledgeStateSlice).service,
            clock=app_state.clock,
        )
        tool_factory = build_research_tool_factory(
            service=service, clock=app_state.clock
        )
        app_state.swap_slice(
            ResearchStateSlice(service=service, tool_factory=tool_factory)
        )
        logger.info(API_APP_STARTUP, service="research_engine", note="wired")
    except Exception as exc:
        reraise_critical(exc)
        logger.info(
            API_APP_STARTUP,
            service="research_engine",
            note="research engine wiring unavailable; skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_charter_engine(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTracker | None,
) -> None:
    """Wire the deep CEO-interview charter engine behind a provider + persistence."""
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.meta.charter.state import CharterStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(CharterStateSlice).interview_service is not None:
        return
    if (
        provider_registry is None
        or persistence is None
        or app_state.slice(PersistenceStateSlice).backend is None
    ):
        return
    try:
        from synthorg.api.services.project_service import (  # noqa: PLC0415
            ProjectService,
        )
        from synthorg.meta.charter.dispatch import CharterDispatcher  # noqa: PLC0415
        from synthorg.meta.charter.factory import (  # noqa: PLC0415
            build_charter_interview_strategy,
        )
        from synthorg.meta.charter.service import (  # noqa: PLC0415
            CharterInterviewService,
        )
        from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
        from synthorg.persistence.charter_factory import (  # noqa: PLC0415
            build_charter_repository,
        )
        from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
            build_conversational_repositories,
        )

        si_config = await load_self_improvement_config(
            app_state.slice(SettingsStateSlice).settings_service,
        )
        charter_config = si_config.charter
        if not charter_config.interview_enabled:
            return
        charter_repo = build_charter_repository(persistence)
        conv_repos = build_conversational_repositories(persistence)
        available = provider_registry.list_providers()
        if charter_repo is None or conv_repos is None or not available:
            logger.warning(
                CHARTER_SUBSTRATE_UNAVAILABLE,
                note="charter interview enabled but stores/provider unavailable",
            )
            return
        provider = provider_registry.get(available[0])
        strategy = build_charter_interview_strategy(
            charter_config,
            provider=provider,
            cost_tracker=cost_tracker,
        )
        interview_service = CharterInterviewService(
            strategy=strategy,
            config=charter_config,
            conversation_repo=conv_repos.conversation_repo,
            turn_repo=conv_repos.turn_repo,
            charter_repo=charter_repo,
        )
        app_state.swap_slice(CharterStateSlice(interview_service=interview_service))
        budget_slice = app_state.slice(BudgetStateSlice)
        forecast_repo = budget_slice.cost_forecast_repo
        budget_config = budget_slice.budget_config
        if (
            app_state.slice(EngineStateSlice).work_pipeline is None
            or forecast_repo is None
            or budget_config is None
        ):
            logger.warning(
                CHARTER_SUBSTRATE_UNAVAILABLE,
                note="charter dispatcher deps absent; approve will 503",
            )
            return
        resolved_budget = budget_config
        dispatcher = CharterDispatcher(
            charter_repo=charter_repo,
            forecast_repo=forecast_repo,
            project_service=ProjectService(repo=persistence.projects),
            work_pipeline=work_pipeline_of(app_state),
            conversation_repo=conv_repos.conversation_repo,
            budget_currency=lambda: resolved_budget.currency,
        )
        app_state.swap_slice(
            CharterStateSlice(
                interview_service=interview_service, dispatcher=dispatcher
            )
        )
        logger.info(API_APP_STARTUP, service="charter_engine", note="wired")
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            CHARTER_SUBSTRATE_UNAVAILABLE,
            note="charter wiring raised; charter endpoints stay unavailable",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_chief_of_staff_chat(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTracker | None,
) -> None:
    """Wire the Chief of Staff chat backend behind chief_of_staff.chat_enabled."""
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).chief_of_staff_chat is not None:
        return
    if provider_registry is None:
        return
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415

    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    chat_backend = build_chief_of_staff_chat(
        meta_self_improvement.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    if chat_backend is not None:
        app_state.wire(MetaStateSlice, chief_of_staff_chat=chat_backend)
        logger.info(API_APP_STARTUP, service="chief_of_staff_chat", note="wired")


async def _wire_chief_of_staff_proposer(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTracker | None,
    effective_approval_store: ApprovalStoreProtocol,
) -> None:
    """Wire the Chief of Staff proposer behind propose_enabled + persistence.

    Raises:
        ServiceUnavailableError: When propose is enabled against a
            persistent SQLite ApprovalStore (a combination that cannot
            durably persist conversational-intake approvals).
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).chief_of_staff_proposer is not None:
        return
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )

    # Repo wiring must run before the provider-missing early return: a
    # conversational-intake approval from a previous boot still needs the
    # repo to route approve/reject decisions, even without a provider.
    repositories = build_conversational_repositories(persistence)
    if repositories is not None:
        app_state.wire(
            MetaStateSlice,
            conversational_proposal_repo=repositories.proposal_repo,
        )
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="conversational proposal repo wired",
        )
    if provider_registry is None:
        return
    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    # Hard-block the unsupported SQLite + persistent ApprovalStore combo:
    # this schema cannot durably persist conversational-intake approvals.
    store_has_persistent_repo = (
        isinstance(effective_approval_store, ApprovalStore)
        and effective_approval_store.has_persistent_repo
    )
    if (
        meta_self_improvement.chief_of_staff.propose_enabled
        and persistence is not None
        and persistence.backend_name == "sqlite"
        and store_has_persistent_repo
    ):
        msg = (
            "Chief of Staff propose is enabled with a persistent SQLite "
            "ApprovalStore. This combination cannot durably persist "
            "conversational-intake approvals. Switch the backend to "
            "Postgres, or keep ApprovalStore in-memory on SQLite."
        )
        logger.error(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="blocked unsupported proposer configuration",
            backend_name=persistence.backend_name,
            approval_store_type=type(effective_approval_store).__name__,
        )
        raise ServiceUnavailableError(msg)
    # Concern routing (#1969): build the role router when an agent
    # registry is present so a routed turn answers as the right role
    # agent. ``build_role_router`` returns None when routing is off or
    # its strategy's deps are absent, leaving the proposer in v1 generic
    # mode. Stored on the slice so the manifest treats it as wired.
    role_router = None
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if agent_registry is not None:
        from synthorg.meta.chief_of_staff.routing import (  # noqa: PLC0415
            build_role_router,
        )

        role_router = build_role_router(
            config=meta_self_improvement.chief_of_staff,
            provider_registry=provider_registry,
            agent_registry=agent_registry,
            cost_tracker=cost_tracker,
        )
        if role_router is not None:
            app_state.wire(MetaStateSlice, role_router=role_router)
            logger.info(
                API_APP_STARTUP,
                service="chief_of_staff_proposer",
                note="role router wired",
                routing_strategy=str(
                    meta_self_improvement.chief_of_staff.routing_strategy
                ),
            )
    proposer = build_chief_of_staff_proposer(
        meta_self_improvement.chief_of_staff,
        provider_registry=provider_registry,
        approval_store=effective_approval_store,
        repositories=repositories,
        cost_tracker=cost_tracker,
        role_router=role_router,
    )
    if proposer is not None:
        app_state.wire(MetaStateSlice, chief_of_staff_proposer=proposer)
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="proposer wired",
        )


async def wire_features_on_startup(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTracker | None,
    effective_approval_store: ApprovalStoreProtocol,
) -> None:
    """Run every optional feature-engine wire in dependency order."""
    await _wire_docs_engine(app_state)
    await _wire_project_brain(app_state)
    await _wire_knowledge_engine(app_state)
    await _wire_research_engine(app_state, provider_registry=provider_registry)
    await _wire_charter_engine(
        app_state,
        provider_registry=provider_registry,
        persistence=persistence,
        cost_tracker=cost_tracker,
    )
    await _wire_chief_of_staff_chat(
        app_state,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    await _wire_chief_of_staff_proposer(
        app_state,
        provider_registry=provider_registry,
        persistence=persistence,
        cost_tracker=cost_tracker,
        effective_approval_store=effective_approval_store,
    )
    await wire_group_chat_service(
        app_state,
        provider_registry=provider_registry,
        persistence=persistence,
        cost_tracker=cost_tracker,
    )
