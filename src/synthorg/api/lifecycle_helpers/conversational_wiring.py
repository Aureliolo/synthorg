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
    reconcile_orphaned_conversational_intake,
)
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker import CostTracker
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
    cost_tracker: CostTracker | None,
) -> None:
    """Wire the multi-agent group chat behind group_chat_enabled + deps.

    Returns ``None`` (leaving ``POST /meta/chat/group`` at 503) when the
    flag is off, no provider/agent registry is present, or persistence
    is absent. Idempotent: a second boot pass skips when already wired.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
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
    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    repositories = build_conversational_repositories(persistence)
    service = build_group_chat_service(
        meta_self_improvement.chief_of_staff,
        provider_registry=provider_registry,
        agent_registry=agent_registry,
        repositories=repositories,
        cost_tracker=cost_tracker,
        approval_store=app_state.slice(ApprovalStateSlice).store,
    )
    if service is not None:
        app_state.wire(MetaStateSlice, group_chat_service=service)
        logger.info(
            API_APP_STARTUP,
            service="group_chat_service",
            note="group chat wired",
        )


async def wire_conversational_actor(app_state: AppState) -> None:
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
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415
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
    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    actor = build_conversational_actor(
        meta_self_improvement.chief_of_staff,
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


def _guard_conversational_persistence(
    config: ChiefOfStaffConfig,
    persistence: PersistenceBackend | None,
    approval_store: ApprovalStoreProtocol,
) -> None:
    """Fail fast on conversational features over a persistent SQLite store.

    The SQLite ``approvals.source`` CHECK deliberately omits the
    conversational sources (they stay in-memory there), so a propose- or
    invite-produced approval cannot durably persist on SQLite. Block at
    startup with an actionable message rather than letting a parked
    approval silently fail to persist mid-conversation -- the invite
    park's compensation would quietly drop it, which is worse than a
    clear boot error the operator can fix.

    Raises:
        ServiceUnavailableError: When propose or invite is enabled
            against a persistent SQLite ``ApprovalStore``.
    """
    store_has_persistent_repo = (
        isinstance(approval_store, ApprovalStore) and approval_store.has_persistent_repo
    )
    if (
        (config.propose_enabled or config.invite_enabled)
        and persistence is not None
        and persistence.backend_name == "sqlite"
        and store_has_persistent_repo
    ):
        msg = (
            "Chief of Staff propose/invite is enabled with a persistent "
            "SQLite ApprovalStore. This combination cannot durably persist "
            "conversational approvals. Switch the backend to Postgres, or "
            "keep ApprovalStore in-memory on SQLite."
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
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )

    repositories = build_conversational_repositories(persistence)
    if repositories is None:
        return None
    app_state.wire(
        MetaStateSlice,
        conversational_proposal_repo=repositories.proposal_repo,
        conversation_invite_repo=repositories.invite_repo,
        conversation_participant_repo=repositories.participant_repo,
    )
    logger.info(
        API_APP_STARTUP,
        service="chief_of_staff_proposer",
        note="conversational proposal + invite + participant repos wired",
    )
    # Best-effort cleanup: a transient persistence error here must not
    # poison startup (the controllers would simply 503), so a failed
    # reconcile is logged and swallowed rather than crashing the lifespan
    # hook, matching the sibling best-effort wirers.
    try:
        await reconcile_orphaned_conversational_intake(
            repositories, effective_approval_store
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="chief_of_staff_proposer",
            note="orphaned intake reconcile failed; rows kept PENDING",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    return repositories


async def _load_meta_and_guard_persistence(
    app_state: AppState,
    persistence: PersistenceBackend | None,
    effective_approval_store: ApprovalStoreProtocol,
) -> SelfImprovementConfig:
    """Load the self-improvement config and fail fast on bad persistence.

    The guard runs before the provider gate so an enabled propose/invite
    over a persistent SQLite ``ApprovalStore`` fails the boot regardless
    of whether a provider is configured yet -- the combination can never
    durably persist conversational approvals, so it is rejected as an
    unsupported configuration independent of provider presence.

    Returns:
        The loaded ``SelfImprovementConfig``.

    Raises:
        ServiceUnavailableError: When propose or invite is enabled against
            a persistent SQLite ApprovalStore.
    """
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    _guard_conversational_persistence(
        meta_self_improvement.chief_of_staff, persistence, effective_approval_store
    )
    return meta_self_improvement


def _wire_role_router(
    app_state: AppState,
    config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTracker | None,
) -> RoleRouter | None:
    """Build + wire the concern role router when an agent registry is present.

    ``build_role_router`` returns ``None`` when routing is off or its
    strategy's deps are absent, leaving the proposer in v1 generic mode.
    A built router is stored on the slice so the manifest treats it as
    wired.

    Returns:
        The role router, or ``None`` when routing is unavailable.
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if agent_registry is None:
        return None
    from synthorg.meta.chief_of_staff.routing import build_role_router  # noqa: PLC0415

    role_router = build_role_router(
        config=config,
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
            routing_strategy=str(config.routing_strategy),
        )
    return role_router


async def wire_chief_of_staff_proposer(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTracker | None,
    effective_approval_store: ApprovalStoreProtocol,
) -> None:
    """Wire the Chief of Staff proposer behind propose_enabled + persistence.

    Raises:
        ServiceUnavailableError: When propose or invite is enabled against
            a persistent SQLite ApprovalStore (a combination that cannot
            durably persist conversational approvals).
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).chief_of_staff_proposer is not None:
        return
    repositories = await _wire_conversational_repositories_and_reconcile(
        app_state, persistence, effective_approval_store
    )
    # Validate the persistence invariant before the provider gate: an
    # unsupported persistent-SQLite conversational config must fail the
    # boot whether or not a provider is configured yet.
    meta_self_improvement = await _load_meta_and_guard_persistence(
        app_state, persistence, effective_approval_store
    )
    if provider_registry is None:
        return
    role_router = _wire_role_router(
        app_state,
        meta_self_improvement.chief_of_staff,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
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


__all__ = [
    "wire_chief_of_staff_proposer",
    "wire_conversational_actor",
    "wire_group_chat_service",
]
