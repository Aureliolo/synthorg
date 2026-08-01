# module-kind: code
"""On-startup wiring for the conversational write-path services.

Wires the multi-agent group chat behind its feature flag +
runtime dependencies. Lifted out of :mod:`feature_wiring` so the
conversational write-path wirers (the direct-MCP actor wirer joins
here) stay cohesive and ``feature_wiring`` remains a thin dispatcher
under its size tier.
"""

from synthorg.api.app_builders import build_chief_of_staff_proposer
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.conversational_builders import (
    build_conversational_actor,
    build_group_chat_service,
)
from synthorg.api.lifecycle_helpers.conversational_reconcile import (
    reconcile_orphaned_conversational_invites,
)
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.routing import RoleRouter
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.conversational_factory import ConversationalRepositories
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


async def wire_group_chat_service(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the multi-agent group chat behind group_chat_enabled + deps.

    Returns ``None`` (leaving ``POST /meta/chat/group`` at 503) when the
    flag is off, no provider/agent registry is present, or persistence
    is absent. Idempotent: a second boot pass skips when already wired.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).group_chat_service is not None:
        return
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if provider_registry is None or agent_registry is None:
        return
    repositories = build_conversational_repositories(persistence)
    service = build_group_chat_service(
        si_config.chief_of_staff,
        provider_registry=provider_registry,
        agent_registry=agent_registry,
        repositories=repositories,
        cost_tracker=cost_tracker,
        approval_store=app_state.slice(ApprovalStateSlice).store,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        master_enabled=si_config.chief_of_staff_enabled,
    )
    if service is not None:
        app_state.wire(MetaStateSlice, group_chat_service=service)
        logger.info(
            API_APP_STARTUP,
            service="group_chat_service",
            note="group chat wired",
        )


async def wire_conversational_actor(
    app_state: AppState,
    *,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the direct-MCP conversational actor behind direct_mcp_enabled.

    Reuses the SHARED boot ``AgentEngine`` (held by the
    ``AgentEngineExecutionService``) so a sensitive chat action parks on
    the same ``ApprovalGate`` the ``/approvals`` controller resumes.
    Returns ``None`` -- leaving ``POST /meta/chat/act`` at 503 -- when the
    flag is off, no agent registry is present, or no provider-backed boot
    engine was installed (empty company). Idempotent: a second boot pass
    skips when already wired.
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.workers.execution_service import (  # noqa: PLC0415
        AgentEngineExecutionService,
    )
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).conversational_actor is not None:
        return
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if agent_registry is None:
        return
    # Read the slice directly (not ``worker_execution_service_of``) to
    # avoid the lazy fallback that builds a lifecycle-only
    # ``LifecycleAdvancingExecutionService`` (no real agent engine): a
    # not-yet-installed service must stay ``None`` here, because only a
    # real boot ``AgentEngineExecutionService`` can drive an MCP action.
    service = app_state.slice(RuntimeStateSlice).worker_execution_service
    if not isinstance(service, AgentEngineExecutionService):
        return
    actor = build_conversational_actor(
        si_config.chief_of_staff,
        engine=service.engine,
        agent_registry=agent_registry,
        autonomy_resolver=service.autonomy_resolver,
    )
    if actor is not None:
        app_state.wire(MetaStateSlice, conversational_actor=actor)
        logger.info(
            API_APP_STARTUP,
            service="conversational_actor",
            note="direct MCP actor wired",
        )


async def unwire_conversational_actor(app_state: AppState) -> None:
    """Take the direct-MCP actor down so the next pass rebuilds it.

    The reconciler pairs this with the wirer above on any change to
    ``direct_mcp_enabled``, which is what makes the toggle live: teardown,
    then a rebuild that re-runs the same fail-closed
    :func:`build_conversational_actor` gate (governance and the MCP
    self-consumer must be wired on the boot engine). A live enable therefore
    stays fail-closed, and a live disable genuinely removes the actor instead
    of leaving the previous instance acting.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    app_state.wire(MetaStateSlice, conversational_actor=None)
    logger.info(
        API_APP_STARTUP,
        service="conversational_actor",
        note="direct MCP actor unwired",
    )


def _guard_conversational_persistence(
    config: ChiefOfStaffConfig,
    persistence: PersistenceBackend | None,
    approval_store: ApprovalStoreProtocol,
) -> None:
    """Fail fast on conversational features over a non-supporting store.

    A backend whose ``supports_conversational_approvals`` predicate is
    ``False`` cannot durably persist a propose- or invite-produced
    approval. Block at startup with an actionable message rather than
    letting a parked approval silently fail to persist mid-conversation:
    the invite park's compensation would quietly drop it, which is worse
    than a clear boot error the operator can fix. Both shipped backends
    (SQLite and Postgres) advertise support, so this is a forward-looking
    capability guard rather than a live constraint on either of them.

    Raises:
        ServiceUnavailableError: When propose or invite is enabled
            against a persistent ``ApprovalStore`` on a backend that does
            not support conversational approvals.
    """
    store_has_persistent_repo = (
        isinstance(approval_store, ApprovalStore) and approval_store.has_persistent_repo
    )
    if (
        (config.propose_enabled or config.invite_enabled)
        and persistence is not None
        and not persistence.supports_conversational_approvals
        and store_has_persistent_repo
    ):
        msg = (
            "Chief of Staff propose/invite is enabled with a persistent "
            f"ApprovalStore on backend '{persistence.backend_name}', which "
            "cannot durably persist conversational approvals. Use a backend "
            "that supports them, or keep the ApprovalStore in-memory."
        )
        logger.error(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="blocked unsupported conversational persistence configuration",
            backend_name=persistence.backend_name,
            approval_store_type=type(approval_store).__name__,
        )
        raise ServiceUnavailableError(msg)


async def _wire_conversational_repositories_and_reconcile(
    app_state: AppState,
    persistence: PersistenceBackend | None,
    effective_approval_store: ApprovalStoreProtocol,
) -> ConversationalRepositories | None:
    """Wire proposal/invite/participant repos and retire orphaned intake.

    Runs before any provider/feature gate: a conversational-intake
    approval (or agent-invite consent) from a previous boot still needs
    its repo to route approve/reject decisions, even without a provider
    and even when the invite feature is now off -- so the invite repo
    wires here, ungated, alongside the proposal repo.

    Returns:
        The repositories, or ``None`` when persistence is absent / not
        connected.
    """
    from synthorg.meta.chief_of_staff.resume_service import (  # noqa: PLC0415
        ConversationalResumeService,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )

    repositories = build_conversational_repositories(persistence)
    if repositories is None:
        return None
    # The resume flows route every invite/participant repo call through this
    # ungated facade, so it is wired alongside the repos it wraps
    # (toggle-independent: a decided conversational approval still resolves
    # after the propose/invite features are switched off).
    app_state.wire(
        MetaStateSlice,
        conversation_invite_repo=repositories.invite_repo,
        conversation_participant_repo=repositories.participant_repo,
        conversational_resume_service=ConversationalResumeService(
            invite_repo=repositories.invite_repo,
            participant_repo=repositories.participant_repo,
            conversation_repo=repositories.conversation_repo,
            turn_repo=repositories.turn_repo,
        ),
    )
    logger.info(
        API_APP_STARTUP,
        service="chief_of_staff_proposer",
        note="conversational invite + participant repos + resume service wired",
    )
    # Best-effort cleanup: a transient persistence error here must not
    # poison startup (the controllers would simply 503), so a failed
    # reconcile is logged and swallowed rather than crashing the lifespan
    # hook, matching the sibling best-effort wirers.
    try:
        await reconcile_orphaned_conversational_invites(
            repositories, effective_approval_store
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="orphaned invite reconcile failed; rows kept PENDING",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    return repositories


def _wire_role_router(
    app_state: AppState,
    config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None,
) -> RoleRouter | None:
    """Build + wire the concern role router when an agent registry is present.

    ``build_role_router`` builds the router unconditionally of
    ``routing_enabled`` so the live per-turn routing gate (applied in the
    proposer) can flip without a restart; it returns ``None`` only when the
    chosen strategy's deps are absent, leaving the proposer in v1 generic
    mode. A built router is stored on the slice so the manifest treats it as
    wired.

    Returns:
        The role router, or ``None`` when its strategy's deps are absent.
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if agent_registry is None:
        return None
    from synthorg.meta.chief_of_staff.routing import build_role_router  # noqa: PLC0415

    role_router = build_role_router(
        config=config,
        provider_registry=provider_registry,
        agent_registry=agent_registry,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    if role_router is not None:
        app_state.wire(MetaStateSlice, role_router=role_router)
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="role router wired",
            routing_strategy=str(config.routing_strategy),
        )
    return role_router


def _wire_turn_intent_classifier(
    app_state: AppState,
    config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None,
) -> None:
    """Build + wire the unified turn-intent classifier when a model is set.

    ``build_intent_classifier`` builds the classifier unconditionally of
    ``turn_router_enabled`` so the live per-request gate (applied in the
    ``/meta/chat/turn`` endpoint) can flip without a restart; it returns
    ``None`` only when no ``turn_intent_model`` is configured or its bound
    provider is absent, leaving the unified router to answer every turn as a
    plain question.
    """
    from synthorg.meta.chief_of_staff.intent_router import (  # noqa: PLC0415
        build_intent_classifier,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    classifier = build_intent_classifier(
        config=config,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    if classifier is not None:
        app_state.wire(MetaStateSlice, turn_intent_classifier=classifier)
        logger.info(
            API_APP_STARTUP,
            service="turn_intent_classifier",
            note="turn intent classifier wired",
        )


def _wire_multi_voice_router(
    app_state: AppState,
    config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None,
) -> None:
    """Build + wire the multi-voice chime-in router when a model is set.

    ``build_multi_voice_router`` builds the router unconditionally of
    ``multi_voice_enabled`` so the live per-turn gate can flip without a
    restart; it returns ``None`` only when no ``multi_voice_model`` is set or
    its bound provider is absent, leaving turns to carry no chime-ins.
    """
    from synthorg.meta.chief_of_staff._multi_voice import (  # noqa: PLC0415
        build_multi_voice_router,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    router = build_multi_voice_router(
        config=config,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    if router is not None:
        app_state.wire(MetaStateSlice, multi_voice_router=router)
        logger.info(
            API_APP_STARTUP,
            service="multi_voice_router",
            note="multi-voice router wired",
        )


async def wire_chief_of_staff_proposer(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTrackerProtocol | None,
    effective_approval_store: ApprovalStoreProtocol,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the Chief of Staff proposer behind propose_enabled + persistence.

    Raises:
        ServiceUnavailableError: When propose or invite is enabled against
            a persistent ApprovalStore on a backend that does not support
            conversational approvals.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    # The turn-intent classifier and multi-voice router are optional and each
    # guards its own state field idempotently, so wire them on every pass -- even
    # once the proposer exists -- so a classifier / multi-voice model configured
    # after the proposer was first built still wires without a restart.
    if provider_registry is not None:
        _wire_turn_intent_classifier(
            app_state,
            si_config.chief_of_staff,
            provider_registry=provider_registry,
            cost_tracker=cost_tracker,
        )
        _wire_multi_voice_router(
            app_state,
            si_config.chief_of_staff,
            provider_registry=provider_registry,
            cost_tracker=cost_tracker,
        )

    if app_state.slice(MetaStateSlice).chief_of_staff_proposer is not None:
        return
    repositories = await _wire_conversational_repositories_and_reconcile(
        app_state, persistence, effective_approval_store
    )
    # Validate the persistence invariant before the provider gate: a
    # persistent ApprovalStore on a backend that does not support
    # conversational approvals must fail the boot whether or not a provider
    # is configured yet.
    _guard_conversational_persistence(
        si_config.chief_of_staff, persistence, effective_approval_store
    )
    if provider_registry is None:
        return
    role_router = _wire_role_router(
        app_state,
        si_config.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    proposer = build_chief_of_staff_proposer(
        si_config.chief_of_staff,
        provider_registry=provider_registry,
        approval_store=effective_approval_store,
        repositories=repositories,
        cost_tracker=cost_tracker,
        role_router=role_router,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        master_enabled=si_config.chief_of_staff_enabled,
    )
    if proposer is not None:
        app_state.wire(MetaStateSlice, chief_of_staff_proposer=proposer)
        logger.info(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="proposer wired",
        )


async def wire_conversational_plan_dispatcher(app_state: AppState) -> None:
    """Attach the plan dispatcher to the proposer once its deps are up.

    The proposer needs the work pipeline (to intake the objective and drive
    the decompose+park spine), the project store (to provision the
    project), and the worker execution service (to background that spine).
    All three wire after the proposer is constructed, so this late-bind
    hook attaches the dispatcher afterwards. A missing pipeline or store
    leaves the proposer unable to draft a plan (its act path 503s); a
    missing worker service degrades to a synchronous decompose+park.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.meta.chief_of_staff.plan_intake import (  # noqa: PLC0415
        ConversationalPlanDispatcher,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    proposer = app_state.slice(MetaStateSlice).chief_of_staff_proposer
    if proposer is None:
        return
    work_pipeline = app_state.slice(EngineStateSlice).work_pipeline
    backend = app_state.slice(PersistenceStateSlice).backend
    if work_pipeline is None or backend is None:
        logger.warning(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="plan dispatcher skipped: work pipeline or persistence not wired",
        )
        return
    dispatcher = ConversationalPlanDispatcher(
        project_repo=backend.projects,
        work_pipeline=work_pipeline,
        clock=app_state.clock,
        dispatch_port=app_state.slice(RuntimeStateSlice).worker_execution_service,
    )
    proposer.attach_plan_dispatcher(dispatcher)
    logger.info(
        API_APP_STARTUP,
        service="chief_of_staff_proposer",
        note="plan dispatcher attached",
    )


__all__ = [
    "unwire_conversational_actor",
    "wire_chief_of_staff_proposer",
    "wire_conversational_actor",
    "wire_conversational_plan_dispatcher",
    "wire_group_chat_service",
]
