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
from synthorg.api.subsystems.errors import SubsystemDeclinedError
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


class ConversationalApprovalsUnsupportedError(ServiceUnavailableError):
    """The backend cannot durably hold a conversational approval.

    Distinct from the bare ``ServiceUnavailableError`` its parent raises so
    the subsystem activation can catch the deliberate refusal without also
    catching a genuine wiring fault: ``require_service`` reports an absent
    collaborator with the same base type, and treating that as an intended
    refusal would leave the subsystem reading BLOCKED instead of FAILED,
    with nothing naming what is actually broken.
    """


async def wire_group_chat_service(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTrackerProtocol | None,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the multi-agent group chat behind group_chat_enabled + deps.

    Leaves ``POST /meta/chat/group`` at 503 when the flag is off, no
    provider/agent registry is present, or persistence is absent.
    Idempotent: a second boot pass skips when already wired.

    Raises:
        SubsystemDeclinedError: No provider registry or no agent registry,
            so there is nobody to convene and nothing to convene them with.
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
    if provider_registry is None:
        msg = "no provider registry; every voice in the room is an LLM call"
        raise SubsystemDeclinedError(msg)
    if agent_registry is None:
        msg = "no agent registry; there is nobody to convene"
        raise SubsystemDeclinedError(msg)
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
    Idempotent: a second boot pass skips when already wired.

    Raises:
        SubsystemDeclinedError: The actor cannot be built, naming which
            precondition refused. Leaves ``POST /meta/chat/act`` at 503, and
            gives ``GET /subsystems`` the reason to report instead of a
            BLOCKED with nothing to look at.
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
        msg = "no agent registry is wired, so there is nobody to act as"
        raise SubsystemDeclinedError(msg)
    # Read the slice directly (not ``worker_execution_service_of``) to
    # avoid the lazy fallback that builds a lifecycle-only
    # ``LifecycleAdvancingExecutionService`` (no real agent engine): a
    # not-yet-installed service must stay ``None`` here, because only a
    # real boot ``AgentEngineExecutionService`` can drive an MCP action.
    service = app_state.slice(RuntimeStateSlice).worker_execution_service
    if not isinstance(service, AgentEngineExecutionService):
        msg = (
            "no provider-backed boot engine is installed, so no agent can "
            "drive an MCP action (an empty company reaches this)"
        )
        raise SubsystemDeclinedError(msg)
    # The builder is fail-closed and raises with the condition that fired,
    # so there is nothing to guess at here.
    actor = build_conversational_actor(
        si_config.chief_of_staff,
        engine=service.engine,
        agent_registry=agent_registry,
        autonomy_resolver=service.autonomy_resolver,
    )
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
        ConversationalApprovalsUnsupportedError: When propose or invite is
            enabled against a persistent ``ApprovalStore`` on a backend that
            does not support conversational approvals.
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
        raise ConversationalApprovalsUnsupportedError(msg)


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
        ConversationalApprovalsUnsupportedError: When propose or invite is
            enabled against a persistent ApprovalStore on a backend that does
            not support conversational approvals. Named precisely because a
            caller has to catch the refusal without also catching the shared
            503 base a genuine wiring fault raises.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

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


async def unwire_chief_of_staff_proposer(app_state: AppState) -> None:
    """Take the proposer and its role router down so a pass can rebuild them.

    Both bake their model choice in at construction, so replacing the
    instance is what makes ``propose_model`` and ``routing_model`` live in
    both directions: naming a model brings the proposer up, and changing or
    clearing one replaces it rather than leaving the previous instance
    answering on its build-time pair.

    The conversational repositories and the resume service stay wired on
    purpose. They are ungated: a decided intake approval from a previous
    boot still needs its repository to route the decision, even once the
    propose feature is switched off.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    app_state.wire(MetaStateSlice, chief_of_staff_proposer=None, role_router=None)
    logger.info(
        API_APP_STARTUP,
        service="chief_of_staff_proposer",
        note="proposer unwired",
    )


__all__ = [
    "unwire_chief_of_staff_proposer",
    "unwire_conversational_actor",
    "wire_chief_of_staff_proposer",
    "wire_conversational_actor",
    "wire_group_chat_service",
]
